"""Voice for Buddy on macOS: Apple's on-device speech recognition in, the built-in system voices out.

Both are part of macOS, so nothing is downloaded. macOS asks once for the Microphone and Speech
Recognition permissions (the app's Info.plist explains why).

Hold-to-talk listens in the language picked in the menu (Voice language), or the Mac's own language. Answers
are read with a system voice for the language they are written in, so an Indonesian answer is not read
with an English voice.
"""
from __future__ import annotations

import logging

from PyObjCTools import AppHelper

from . import core


def text_language(text: str, preferred: str = "") -> str:
    """The language a text is written in ("id", "en", "zh-Hant"), or "" if unsure.

    Uses Apple's NaturalLanguage recognizer; the older NSLinguisticTagger reads Indonesian as Turkish. When
    the hold-to-talk language (preferred) is among the likely languages, it wins: short answers are often
    ambiguous between close languages (Indonesian and Malay).
    """
    text = text or ""
    try:
        from NaturalLanguage import NLLanguageRecognizer

        recognizer = NLLanguageRecognizer.alloc().init()
        wanted = core.primary(preferred)
        if wanted:
            recognizer.setLanguageHints_({wanted: 0.6})
        recognizer.processString_(text)
        hypotheses = {str(code): float(chance) for code, chance in
                      (recognizer.languageHypothesesWithMaximum_(3) or {}).items()}
        if wanted and hypotheses.get(wanted, 0.0) >= 0.2:
            return wanted
        if hypotheses:
            best = max(hypotheses, key=hypotheses.get)
            return best if hypotheses[best] >= 0.5 and best != "und" else ""
        return ""
    except Exception:                                       # noqa: BLE001
        logging.info("NaturalLanguage unavailable; using NSLinguisticTagger")
    try:
        from Foundation import NSLinguisticTagger

        language = str(NSLinguisticTagger.dominantLanguageForString_(text) or "")
    except Exception:                                       # noqa: BLE001
        logging.exception("language detection unavailable")
        return ""
    return "" if language == "und" else language


class Speaker:
    """Reads answers aloud with AVSpeechSynthesizer (falls back to NSSpeechSynthesizer)."""

    def __init__(self):
        self.synth = None
        self.kind = ""
        try:
            from AVFoundation import AVSpeechSynthesizer

            self.synth = AVSpeechSynthesizer.alloc().init()
            self.kind = "av"
        except Exception:                                   # noqa: BLE001
            try:
                from AppKit import NSSpeechSynthesizer

                self.synth = NSSpeechSynthesizer.alloc().initWithVoice_(None)
                self.kind = "ns"
            except Exception:                               # noqa: BLE001
                logging.exception("no speech synthesizer")

    @property
    def ok(self) -> bool:
        return self.synth is not None

    def voice_for(self, text: str, preferred: str = ""):
        """The AVSpeechSynthesisVoice to read `text` with, or None to keep the user's default voice."""
        if self.kind != "av":
            return None
        from AVFoundation import AVSpeechSynthesisVoice

        language = text_language(text, preferred) or preferred
        default = str(AVSpeechSynthesisVoice.currentLanguageCode() or "")
        if not language or core.primary(language) == core.primary(default):
            return None                                     # the user's own default voice speaks it
        voices = AVSpeechSynthesisVoice.speechVoices() or []
        listed = [(str(voice.identifier()), str(voice.language()), int(voice.quality())) for voice in voices]
        identifier = core.pick_voice(language, listed, preferred)
        if identifier is None:
            logging.info("no system voice for %s; reading with the default voice", language)
            return None
        return AVSpeechSynthesisVoice.voiceWithIdentifier_(identifier)

    def speak(self, text: str, preferred: str = ""):
        """Read `text` aloud. preferred: the hold-to-talk language, used to pick a region's voice."""
        if not self.synth or not text:
            return
        self.stop()
        if self.kind == "av":
            from AVFoundation import AVSpeechUtterance

            utterance = AVSpeechUtterance.speechUtteranceWithString_(text)
            try:
                voice = self.voice_for(text, preferred)
            except Exception:                               # noqa: BLE001
                logging.exception("choosing a voice failed")
                voice = None
            if voice is not None:
                utterance.setVoice_(voice)
            self.synth.speakUtterance_(utterance)
        else:
            self.synth.startSpeakingString_(text)

    def stop(self):
        if not self.synth:
            return
        if self.kind == "av":
            self.synth.stopSpeakingAtBoundary_(0)          # AVSpeechBoundaryImmediate
        else:
            self.synth.stopSpeaking()


class Listener:
    """Hold-to-talk: start() opens the mic, stop() ends it and delivers the final text to on_done(text)."""

    def __init__(self, on_partial, on_done, on_error, language: str = ""):
        self.on_partial = on_partial
        self.on_done = on_done
        self.on_error = on_error
        self.engine = None
        self.request = None
        self.task = None
        self.text = ""
        self.finished = False
        self.recognizer = None
        self.authorized = None
        self.language = ""
        self.set_language(language)

    @staticmethod
    def supported_languages() -> set[str]:
        """Locale codes Apple's speech recognition knows on this Mac, as "id-ID" (some only work online)."""
        try:
            from Speech import SFSpeechRecognizer

            return {str(locale.localeIdentifier()).replace("_", "-") for locale in SFSpeechRecognizer.supportedLocales()}
        except Exception:                                   # noqa: BLE001
            logging.exception("speech recognition languages unavailable")
            return set()

    def set_language(self, code: str) -> bool:
        """Listen in `code` ("id-ID"); "" = the Mac's own language. False (and the Mac's language) if unknown."""
        try:
            from Speech import SFSpeechRecognizer

            recognizer = None
            if code:
                from Foundation import NSLocale

                locale = NSLocale.localeWithLocaleIdentifier_(code.replace("-", "_"))
                recognizer = SFSpeechRecognizer.alloc().initWithLocale_(locale)
                if recognizer is None:
                    logging.warning("speech recognition has no %s; using the Mac's language", code)
            self.language = code if recognizer is not None else ""
            self.recognizer = recognizer or SFSpeechRecognizer.alloc().init()
        except Exception:                                   # noqa: BLE001
            logging.exception("speech recognition unavailable")
            self.recognizer = None
            self.language = ""
        return self.language == code

    @property
    def ok(self) -> bool:
        return self.recognizer is not None

    def request_permission(self):
        """Ask for Speech Recognition (and, through the first recording, Microphone) permission."""
        if not self.recognizer:
            return
        from Speech import SFSpeechRecognizer

        def answered(status):
            self.authorized = status == 3                   # SFSpeechRecognizerAuthorizationStatusAuthorized
            logging.info("speech recognition permission: %s", status)

        SFSpeechRecognizer.requestAuthorization_(answered)

    def start(self) -> bool:
        if not self.recognizer:
            self.on_error("Speech recognition isn't available on this Mac. Tap the hotkey to type instead.")
            return False
        try:
            from AVFoundation import AVAudioEngine
            from Speech import SFSpeechAudioBufferRecognitionRequest

            self.text = ""
            self.finished = False
            self.engine = AVAudioEngine.alloc().init()
            self.request = SFSpeechAudioBufferRecognitionRequest.alloc().init()
            self.request.setShouldReportPartialResults_(True)
            # On-device when this Mac has the language offline (private, works without internet); otherwise
            # Apple's servers do it, as with dictation in that language.
            if self.recognizer.supportsOnDeviceRecognition():
                self.request.setRequiresOnDeviceRecognition_(True)
            node = self.engine.inputNode()
            audio_format = node.outputFormatForBus_(0)
            request = self.request

            def tap(buffer, when):
                request.appendAudioPCMBuffer_(buffer)

            node.installTapOnBus_bufferSize_format_block_(0, 1024, audio_format, tap)
            self.engine.prepare()
            ok, error = self.engine.startAndReturnError_(None)
            if not ok:
                raise RuntimeError(f"microphone didn't start: {error}")
            self.task = self.recognizer.recognitionTaskWithRequest_resultHandler_(self.request, self._result)
            return True
        except Exception as error:                          # noqa: BLE001
            logging.exception("listening failed")
            self._teardown()
            self.on_error(f"I couldn't open the microphone ({error}). Check System Settings, Privacy.")
            return False

    def _result(self, result, error):
        if result is not None:
            self.text = result.bestTranscription().formattedString()
            AppHelper.callAfter(self.on_partial, self.text)
            if result.isFinal():
                self._deliver()
        elif error is not None and not self.finished:
            logging.info("recognition ended: %s", error)
            self._deliver()

    def stop(self):
        """Key released: stop recording; the final text arrives through on_done shortly after."""
        if self.engine is None:
            return
        self._teardown(keep_task=True)
        if self.request is not None:
            self.request.endAudio()
        # If the recognizer never sends a final result, deliver what we have after a moment
        AppHelper.callLater(1.5, self._deliver)

    def _deliver(self):
        if self.finished:
            return
        self.finished = True
        text = self.text.strip()
        if self.task is not None:
            self.task.cancel()
            self.task = None
        AppHelper.callAfter(self.on_done, text)

    def _teardown(self, keep_task=False):
        if self.engine is not None:
            try:
                self.engine.stop()
                self.engine.inputNode().removeTapOnBus_(0)
            except Exception:                               # noqa: BLE001
                pass
            self.engine = None
        if not keep_task and self.task is not None:
            self.task.cancel()
            self.task = None
