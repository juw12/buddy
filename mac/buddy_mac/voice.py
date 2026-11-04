"""Voice for Buddy on macOS: Apple's on-device speech recognition in, the built-in system voices out.

Both are part of macOS, so nothing is downloaded. macOS asks once for the Microphone and Speech
Recognition permissions (the app's Info.plist explains why).
"""
from __future__ import annotations

import logging

from PyObjCTools import AppHelper


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

    def speak(self, text: str):
        if not self.synth or not text:
            return
        self.stop()
        if self.kind == "av":
            from AVFoundation import AVSpeechUtterance

            utterance = AVSpeechUtterance.speechUtteranceWithString_(text)
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

    def __init__(self, on_partial, on_done, on_error):
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
        try:
            from Speech import SFSpeechRecognizer

            self.recognizer = SFSpeechRecognizer.alloc().init()
        except Exception:                                   # noqa: BLE001
            logging.exception("speech recognition unavailable")

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
