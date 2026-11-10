"""Unit tests: recorder alignment/pack building, pack loading, keyboard filter logic.

Run:  python -m unittest discover -s tests -v
"""
import json
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ["SDL_AUDIODRIVER"] = "dummy"          # no sound card needed (CI)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

import keyit_recorder as recorder  # noqa: E402

SR = recorder.SAMPLE_RATE


def synthetic_click(rng, peak, length_s=0.06):
    t = np.arange(int(length_s * SR)) / SR
    burst = rng.standard_normal(t.size) * np.exp(-t / 0.002)
    body = np.sin(2 * np.pi * rng.uniform(200, 400) * t) * np.exp(-t / 0.015)
    sound = burst + body
    return peak * sound / np.abs(sound).max()


def fake_session(latency_s=0.037, seed=3):
    """Build a fake mic recording + key events with a known latency."""
    rng = np.random.default_rng(seed)
    codes = [30, 31, 32, 33, 57, 28, 14, 42, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25] * 2
    audio_start = 1000.0
    audio = rng.standard_normal(int(14 * SR)).astype(np.float32) * 0.002
    events, true_onsets = [], []
    t = audio_start + 0.5
    for i, code in enumerate(codes):
        t += rng.uniform(0.12, 0.2) if i % 4 else rng.uniform(0.35, 0.5)  # some isolated keys
        up_t = t + rng.uniform(0.07, 0.1)
        for when, is_down, peak in ((t, True, 0.5), (up_t, False, 0.2)):
            onset = int((when - audio_start + latency_s) * SR)
            clip = synthetic_click(rng, peak, 0.06 if is_down else 0.04)
            audio[onset:onset + clip.size] += clip
            events.append((code, is_down, when))
            true_onsets.append(onset)
        t = up_t
    return audio, audio_start, events, true_onsets


class RecorderTests(unittest.TestCase):
    def test_latency_and_onsets(self):
        audio, start, events, true_onsets = fake_session(latency_s=0.037)
        clips, info = recorder.slice_keystrokes(audio, events, start)
        self.assertAlmostEqual(info["latency_ms"], 37.0, delta=2.0)
        order = np.argsort([e[2] for e in events])
        errors_ms = [abs(info["onsets"][k] - true_onsets[i]) * 1000 / SR for k, i in enumerate(order)]
        self.assertGreaterEqual(np.mean(np.array(errors_ms) < 2.0), 0.9, f"onset errors: {errors_ms}")
        self.assertEqual(info["skipped"], 0)

    def test_build_pack(self):
        audio, start, events, _ = fake_session()
        clips, _ = recorder.slice_keystrokes(audio, events, start)
        with tempfile.TemporaryDirectory() as root:
            pack_dir, report = recorder.build_pack(clips, "Test Board", root, reserved_names={"test-board"})
            self.assertEqual(pack_dir.name, "test-board-2")           # reserved name avoided
            config = json.loads((pack_dir / "config.json").read_text())
            self.assertEqual(config["key_define_type"], "multi")
            self.assertEqual(report["recorded_down_keys"], 18)
            self.assertEqual(report["missing_important"], [])
            for code in recorder.STANDARD_CODES:                         # Mechvibes-complete
                self.assertIn(str(code), config["defines"])
                self.assertTrue((pack_dir / config["defines"][str(code)]).is_file())

            # The pack must load and play through the real player code
            import keyit_core as core
            import pygame

            pygame.mixer.pre_init(core.MIXER_RATE, -16, 2, core.MIXER_BUFFER)
            pygame.mixer.init()
            pack = core.SoundPack(pack_dir)
            self.assertIn(57, pack.down)
            self.assertIn(57, pack.up)
            self.assertIsNotNone(pack.lookup(9999, True))                # fallback pool
            pygame.mixer.quit()


class KeyboardFilterTests(unittest.TestCase):
    def setUp(self):
        import keyit_core as core

        self.core = core
        core.save_settings = lambda settings: None
        self.sent = []
        player = types.SimpleNamespace(
            settings={"enabled": True},
            submit=lambda code, is_down: self.sent.append((code, is_down)),
        )
        self.hook = core.KeyboardHook.__new__(core.KeyboardHook)
        self.hook.player, self.hook.on_toggle, self.hook.observers, self.hook.held = player, None, [], {}
        self.hook.buddy, self.hook.buddy_active = None, False
        self.suppressed = []

        class Suppressed(Exception):
            pass

        self.Suppressed = Suppressed

        def suppress_event():
            self.suppressed.append(True)
            raise Suppressed()

        self.hook.listener = types.SimpleNamespace(suppress_event=suppress_event)

    def key(self, msg, scan, extended=False):
        try:
            self.hook._filter(msg, types.SimpleNamespace(scanCode=scan, vkCode=0, flags=1 if extended else 0))
        except self.Suppressed:
            pass

    def test_auto_repeat_ignored(self):
        for _ in range(5):
            self.key(0x100, 30)
        self.key(0x101, 30)
        self.assertEqual(self.sent, [(30, True), (30, False)])

    def test_extended_and_fake_shift(self):
        self.key(0x100, 0x48, extended=True)
        self.key(0x100, 0x2A, extended=True)
        self.assertEqual(self.sent, [(57416, True)])

    def test_mute_hotkey(self):
        for scan in (0x1D, 0x38, 0x32):
            self.key(0x100, scan)
        self.assertFalse(self.hook.player.settings["enabled"])

    def test_observers_receive_events(self):
        seen = []
        self.hook.observers.append(lambda code, is_down, t: seen.append((code, is_down)))
        self.key(0x100, 57)
        self.key(0x101, 57)
        self.assertEqual(seen, [(57, True), (57, False)])

    def test_buddy_push_to_talk(self):
        import threading
        import time

        calls = []
        self.hook.buddy = types.SimpleNamespace(
            press=lambda: calls.append("press"),
            release=lambda: calls.append("release"),
            cancel=lambda: calls.append("cancel"),
        )
        self.key(0x100, 0x1D)                # Ctrl
        self.key(0x100, 0x38)                # Alt
        for _ in range(4):                   # Space + auto-repeats while talking
            self.key(0x100, 57)
        self.key(0x101, 57)
        time.sleep(0.05)
        self.assertEqual(calls, ["press", "release"])
        self.assertEqual(len(self.suppressed), 5)            # no spaces typed into the app
        self.assertNotIn((57, True), self.sent)              # and no key sound for them
        self.key(0x100, 57)                                  # plain Space (no modifiers held? Ctrl/Alt still held)
        self.key(0x101, 0x1D)
        self.key(0x101, 0x38)

    def test_plain_space_untouched_with_buddy(self):
        self.hook.buddy = types.SimpleNamespace(press=lambda: None, release=lambda: None, cancel=lambda: None)
        self.key(0x100, 57)
        self.key(0x101, 57)
        self.assertEqual(self.suppressed, [])
        self.assertEqual(self.sent, [(57, True), (57, False)])


    def test_buddy_dictation_hotkey(self):
        import time

        calls = []
        self.hook.buddy = types.SimpleNamespace(
            press=lambda: calls.append("press"), release=lambda: calls.append("release"),
            cancel=lambda: calls.append("cancel"),
            dictate_press=lambda: calls.append("dictate_press"),
            dictate_release=lambda: calls.append("dictate_release"),
        )
        self.key(0x100, 0x1D)                # Ctrl
        self.key(0x100, 0x38)                # Alt
        for _ in range(3):                   # D + auto-repeats while talking
            self.key(0x100, 32)
        self.key(0x101, 32)
        time.sleep(0.05)
        self.assertEqual(calls, ["dictate_press", "dictate_release"])
        self.assertEqual(len(self.suppressed), 4)            # no "d" typed into the app
        self.assertNotIn((32, True), self.sent)
        self.key(0x101, 0x1D)
        self.key(0x101, 0x38)

    def test_plain_d_untouched_and_old_buddy_objects_still_work(self):
        self.hook.buddy = types.SimpleNamespace(press=lambda: None, release=lambda: None, cancel=lambda: None)
        self.key(0x100, 0x1D)
        self.key(0x100, 0x38)
        self.key(0x100, 32)                  # Ctrl+Alt+D without a dictation-capable Buddy: passes through
        self.key(0x101, 32)
        self.assertEqual(self.suppressed, [])
        self.key(0x101, 0x1D)
        self.key(0x101, 0x38)
        self.key(0x100, 32)                  # plain D
        self.key(0x101, 32)
        self.assertIn((32, True), self.sent)


class BuddyParsingTests(unittest.TestCase):
    def setUp(self):
        import keyit_buddy

        self.buddy = keyit_buddy

    def test_parse_points_and_clean_speech(self):
        reply = ("Click the **Insert** tab, then choose Table. "
                 "[POINT:120,45:Insert tab] [POINT: 300.5 , 210 : Table button]")
        spoken, points = self.buddy.parse_reply(reply)
        self.assertEqual(spoken, "Click the Insert tab, then choose Table.")
        self.assertEqual(points, [(120.0, 45.0, "Insert tab"), (300.5, 210.0, "Table button")])

    def test_out_of_range_and_malformed_tags(self):
        spoken, points = self.buddy.parse_reply("It's up top. [POINT:1500,20:bad] [POINT:50")
        self.assertEqual(spoken, "It's up top.")
        self.assertEqual(points, [])

    def test_to_screen_on_second_monitor(self):
        monitor = {"left": 1920, "top": -200, "width": 2560, "height": 1440}
        self.assertEqual(self.buddy.to_screen((500, 500, "c"), monitor), (3200, 520, "c"))
        self.assertEqual(self.buddy.to_screen((0, 1000, "bl"), monitor), (1920, 1240, "bl"))

    def test_resample_length_and_pitch(self):
        t = np.arange(44100) / 44100
        tone = np.sin(2 * np.pi * 440 * t).astype(np.float32)
        out = self.buddy.resample(tone, 44100, 16000)
        self.assertEqual(out.size, 16000)
        spectrum = np.abs(np.fft.rfft(out))
        self.assertAlmostEqual(np.argmax(spectrum) * 16000 / out.size, 440, delta=2)

    def sentences(self, text, pattern=None):
        """Split the way the streaming reply loop does: (sentences spoken at once, what waits for the end)."""
        pattern = pattern or self.buddy.FINISHED_SENTENCE
        found, upto = [], 0
        while True:
            match = pattern.match(text, upto)
            if not match:
                return found, text[upto:]
            found.append(match.group(1))
            upto = match.end()

    def test_chinese_sentences_are_spoken_one_by_one(self):
        # no space follows a Chinese full stop, so 1.2.0 waited for the whole answer before it spoke
        self.assertEqual(self.sentences("好的。点击保存按钮！然后关闭窗口"),
                         (["好的。", "点击保存按钮！"], "然后关闭窗口"))
        # a closing quote belongs to its sentence, and "?!" is one ending
        self.assertEqual(self.sentences("他说“好的。”然后点击保存"),
                         (["他说“好的。”"], "然后点击保存"))
        self.assertEqual(self.sentences("什么？！再试一次"), (["什么？！"], "再试一次"))
        # the last sentence waits for the end of the answer, like an English one with nothing after it
        self.assertEqual(self.sentences("完成了。"), ([], "完成了。"))
        self.assertEqual(self.sentences("नमस्ते। सेव करें। फिर"),
                         (["नमस्ते।", "सेव करें।"], " फिर"))

    def test_english_sentences_split_exactly_as_before(self):
        before = re.compile(r"\s*(.+?[.!?]+)(?=\s)", re.DOTALL)          # the pattern of Buddy 1.2.0
        samples = ["Click Save. Then close", "Version 3.5 is out", "Done.", "Wait... what? Yes! Go on",
                   "Open File.\nThen pick Export. ", "It costs $3.50 (really). OK", 'She said "stop." Then left']
        for text in samples:
            self.assertEqual(self.sentences(text), self.sentences(text, before), text)
        self.assertEqual(self.sentences("Click Save. Then close"), (["Click Save."], " Then close"))
        self.assertEqual(self.sentences("Version 3.5 is out"), ([], "Version 3.5 is out"))
        self.assertEqual(self.sentences("Done."), ([], "Done."))

    def test_language_rule_only_for_other_languages(self):
        rule = self.buddy.language_rule
        for code in ("en-US", "en-GB", "auto", "eu", None, "", "pt-BR"):
            self.assertEqual(rule(code), "", code)
        self.assertIn("Indonesian", rule("id-ID"))
        self.assertIn("Traditional", rule("zh-TW"))
        self.assertIn("Traditional", rule("zh-HK"))
        self.assertIn("Simplified", rule("zh-CN"))
        self.assertIn("Cantonese", rule("yue"))
        self.assertTrue(rule("ja-JP").startswith("\n- "))
        self.assertIn("Reply in the language of the question", self.buddy.SYSTEM_PROMPT)

    def test_system_prompt_carries_the_language_rule(self):
        def prompt(setting, stt=None):
            buddy = types.SimpleNamespace(stt=stt, settings={"speech_language": setting}, windows=[],
                                          context_provider=None)
            with mock.patch.object(self.buddy.keyit_actions, "list_windows", return_value=[]):
                return self.buddy.Buddy._system_prompt(buddy)

        self.assertIn("voice input language is Indonesian", prompt("id-ID"))
        self.assertTrue(prompt("id-ID").startswith(self.buddy.SYSTEM_PROMPT))
        self.assertNotIn("voice input language", prompt("en-US"))
        # what is being spoken wins over the saved setting
        speaking_japanese = types.SimpleNamespace(spoken_language=lambda: "ja-JP")
        self.assertIn("voice input language is Japanese", prompt("en-US", speaking_japanese))


class SnapTests(unittest.TestCase):
    def test_exact_name_snaps_from_further(self):
        import keyit_targets as kt

        targets = [
            {"name": "Edit", "kind": "menu item", "rect": (200, 70, 250, 100)},
            {"name": "Save As...", "kind": "menu item", "rect": (140, 300, 500, 330)},
        ]
        monitor = (0, 0, 1600, 1000)
        # The free brain put "Edit" a quarter screen off: an exact name still snaps
        self.assertEqual(kt.snap((487, 143, "Edit"), targets, monitor)[1]["name"], "Edit")
        # A partial name that far away does not
        self.assertIsNone(kt.snap((600, 450, "Save"), targets, monitor)[1])
        # Nothing snaps from across the screen
        self.assertIsNone(kt.snap((480, 800, "Edit"), targets, monitor)[1])


if __name__ == "__main__":
    unittest.main()


class TypingTests(unittest.TestCase):
    def test_typing_events_emoji_newline_and_chinese(self):
        import keyit_actions

        events = keyit_actions.typing_events("a\n\U0001F600\u4fdd")
        self.assertEqual(events[0], (0, ord("a"), 0x0004))
        self.assertEqual(events[2], (0x0D, 0, 0))                    # new line = Enter
        self.assertEqual(events[3], (0x0D, 0, 0x0002))
        self.assertEqual([e[1] for e in events[4:8:2]], [0xD83D, 0xDE00])   # emoji = surrogate pair
        self.assertEqual(events[8], (0, 0x4FDD, 0x0004))
        self.assertTrue(all(e[1] <= 0xFFFF for e in events))
        self.assertEqual(len(keyit_actions.typing_events("x\r\ny")), 2 + 2 + 2)

    def test_dictation_spacing(self):
        import keyit_buddy

        self.assertEqual(keyit_buddy.dictation_spacing("Hello there."), "Hello there. ")
        self.assertEqual(keyit_buddy.dictation_spacing("hello"), "hello")
        self.assertEqual(keyit_buddy.dictation_spacing("\u4fdd\u5b58\u3002"), "\u4fdd\u5b58\u3002")

    def test_voice_text_is_typed_in_the_characters_of_the_language_setting(self):
        import keyit_buddy

        class Heard:
            def __init__(self, language):
                self.language = language

            def spoken_language(self):
                return self.language

        def typed(text, language, my_words="", engine_loaded=True):
            buddy = types.SimpleNamespace(
                stt=Heard(language) if engine_loaded else None,
                settings={"my_words": my_words, "voice_cleanup": True, "speech_language": language},
            )
            return keyit_buddy.Buddy._words(buddy, text, "dictate")

        simplified = ("\u8b66\u65b9\u8bf4\u5c38\u4f53\u770b\u8d77\u6765"
                      "\u5df2\u6446\u5728\u90a3\u91cc\u4e00\u5929\u5de6\u53f3")
        traditional = ("\u8b66\u65b9\u8aaa\u5c4d\u9ad4\u770b\u8d77\u4f86"
                       "\u5df2\u64fa\u5728\u90a3\u88e1\u4e00\u5929\u5de6\u53f3")
        self.assertEqual(typed(simplified, "zh-TW"), traditional)
        self.assertEqual(typed(simplified, "yue"), traditional)          # SenseVoice writes Cantonese in Simplified
        self.assertEqual(typed(simplified, "zh-CN"), simplified)
        self.assertEqual(typed(traditional, "zh-CN"), simplified)        # Whisper writes Mandarin in Traditional
        self.assertEqual(typed(simplified, "auto"), simplified)          # auto-detect: as the model wrote it
        self.assertEqual(typed(simplified, "zh-TW", engine_loaded=False), traditional)   # no engine yet: the setting
        self.assertEqual(typed("\u6211\u4eec\u7528\u8f6f\u4ef6\u6d4b\u8bd5", "zh-TW", my_words="\u8f6f\u4ef6"),
                         "\u6211\u5011\u7528\u8f6f\u4ef6\u6e2c\u8a66")       # the spelling in My words wins
        self.assertEqual(typed("Open the S R M page.", "zh-TW", my_words="SRM"), "Open the SRM page.")


class DictationHoldTests(unittest.TestCase):
    """A dictation is decoded while its key is held (keyit_speech.Hold): Buddy starts it, hands it on, ends it."""

    class Inline:
        """Stands in for threading.Thread: the work is done at once, where the test can see it."""

        def __init__(self, target, args=(), daemon=None):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    def setUp(self):
        import keyit_buddy

        self.module = keyit_buddy
        self.held = mock.Mock()
        self.posted = []
        patches = [
            mock.patch.object(keyit_buddy.threading, "Thread", self.Inline),
            mock.patch.object(keyit_buddy, "monitor_under_cursor", return_value=None),
            mock.patch.object(keyit_buddy, "cursor_position", return_value=(0, 0)),
            mock.patch.object(keyit_buddy, "selected_text_quick", return_value="picked"),   # never the real clipboard
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def buddy(self, **extra):
        return types.SimpleNamespace(
            dictation=None,
            overlay=mock.Mock(),
            post=lambda call, *arguments: self.posted.append(call),
            chime=lambda name: None,
            **extra,
        )

    def press(self, speech_engine):
        self.mic = mock.Mock()
        buddy = self.buddy(
            settings={"dictation": True},
            speaker=mock.Mock(),
            _mic=lambda: self.mic,
            _ensure_stt=speech_engine,
        )
        self.module.Buddy.dictate_press(buddy)
        return buddy

    def test_a_press_starts_decoding_from_the_microphone(self):
        stt = mock.Mock()
        stt.hold.return_value = self.held
        buddy = self.press(lambda: stt)
        self.mic.start.assert_called_once_with()
        stt.hold.assert_called_once_with(self.mic)
        self.assertIs(buddy.dictation["held"], self.held)
        self.assertIs(buddy.dictation["mic"], self.mic)
        self.assertEqual(buddy.dictation["selection"], "picked")

    def test_a_press_still_records_when_the_speech_engine_cannot_start(self):
        def broken():
            raise RuntimeError("no engine")

        with self.assertLogs(level="ERROR"):
            buddy = self.press(broken)
        self.assertIsNone(buddy.dictation["held"])           # the error is shown when the key is let go
        self.assertIs(buddy.dictation["mic"], self.mic)

    def test_letting_go_hands_the_hold_to_the_text_step(self):
        seen = []
        buddy = self.buddy(_dictate_text=lambda *arguments: seen.append(arguments))
        audio = np.zeros(1600, dtype=np.float32)
        mic = types.SimpleNamespace(sample_rate=44100, stop=lambda: (audio, 0.0))
        buddy.dictation = {"mic": mic, "held": self.held, "selection": "picked"}
        self.module.Buddy._finish_dictation(buddy, buddy.dictation)
        self.assertIsNone(buddy.dictation)
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0][0], audio)
        self.assertEqual(seen[0][1:], (44100, "picked", self.held))

    def text_step(self, held):
        heard = []
        stt = mock.Mock()
        stt.transcribe.return_value = "from the whole recording"
        stt.waiting.return_value = ""
        buddy = self.buddy(
            stt=stt,
            _ensure_stt=lambda: stt,
            _words=lambda text, mode: heard.append(text) or "",   # nothing to type: the step ends there
        )
        audio = np.zeros(1600, dtype=np.float32)
        self.module.Buddy._dictate_text(buddy, audio, 44100, "", held)
        return heard, stt, audio

    def test_the_text_comes_from_the_hold(self):
        self.held.finish.return_value = "decoded while the key was held"
        heard, stt, audio = self.text_step(self.held)
        self.assertEqual(heard, ["decoded while the key was held"])
        self.held.finish.assert_called_once_with(audio, 44100)
        stt.transcribe.assert_not_called()                   # nothing is decoded twice

    def test_without_a_hold_the_whole_recording_is_decoded(self):
        heard, stt, audio = self.text_step(None)
        self.assertEqual(heard, ["from the whole recording"])
        stt.transcribe.assert_called_once_with(audio, 44100)

    def test_a_microphone_error_ends_the_hold(self):
        def unplugged():
            raise OSError("unplugged")

        buddy = self.buddy()
        buddy.dictation = {"mic": types.SimpleNamespace(sample_rate=44100, stop=unplugged), "held": self.held}
        self.module.Buddy._finish_dictation(buddy, buddy.dictation)
        self.held.cancel.assert_called_once_with()
        self.assertIsNone(buddy.dictation)

    def test_cancelling_a_dictation_ends_the_hold(self):
        mic = mock.Mock()
        buddy = self.buddy(generation=0, speaker=mock.Mock(), _stop_look_ahead=lambda: None, mic=None)
        buddy.dictation = {"mic": mic, "held": self.held}
        self.module.Buddy.cancel(buddy)
        mic.stop.assert_called_once_with()
        self.held.cancel.assert_called_once_with()
        self.assertIsNone(buddy.dictation)
