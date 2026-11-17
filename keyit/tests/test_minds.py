"""Two minds (keyit_minds): when System 1 acts alone, when it hands over, and how it learns.

    python -m pytest keyit/tests/test_minds.py -q
"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench"))

import fast_slow  # noqa: E402
import keyit_buddy  # noqa: E402
import keyit_minds as minds_module  # noqa: E402
from keyit_minds import FAST, SLOW, Minds  # noqa: E402

WINDOWS = fast_slow.WINDOWS


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def practise(minds, phrase, action, times):
    """The brain does `action` for `phrase`, and the user goes on to something else."""
    for _ in range(times):
        minds.learn(phrase, [action])
        minds.heard("what is on my screen")


class System1Tests(unittest.TestCase):
    def setUp(self):
        self.minds = Minds(None)

    def test_a_routine_command_is_done_by_system_1_on_the_window_it_names(self):
        decision = self.minds.decide("close notepad", WINDOWS)
        self.assertEqual((decision.mind, decision.action, decision.window["app"]), (FAST, ("close", "notepad"), "Notepad"))
        self.assertGreaterEqual(decision.confidence, minds_module.STAKES["close"])

    def test_strain_hands_the_request_to_system_2(self):
        for request, why in (("how do I close notepad", "a question"), ("close notepad and open excel", "two steps"),
                             ("don't close notepad", "a negation"), ("close notepad if it's saved", "a condition"),
                             ("close everything", "more than one thing"), ("maybe close outlook", "a doubt"),
                             ("close excel?", "a doubt")):
            decision = self.minds.decide(request, WINDOWS)
            self.assertEqual((decision.mind, decision.why), (SLOW, why), request)
        self.assertEqual(self.minds.decide("close notepad", WINDOWS, selection="some text").why, "text is highlighted")

    def test_a_window_it_cannot_pin_down_is_left_to_system_2(self):
        for request, windows in (("close word", WINDOWS), ("close notepad plus plus", WINDOWS),
                                 ("close notepad", fast_slow.TWO_NOTEPADS), ("minimize this", [])):
            self.assertEqual(self.minds.decide(request, windows).mind, SLOW, request)

    def test_a_browser_tab_called_word_is_not_word(self):
        windows = [fast_slow.window("Microsoft Edge", "msedge", "Word - Microsoft Edge", 9)]
        self.assertEqual(self.minds.decide("close word", windows).mind, SLOW)

    def test_the_bar_rises_with_the_stakes(self):
        self.assertEqual(self.minds.decide("minimize this", WINDOWS).mind, FAST)
        closing = self.minds.decide("close this", WINDOWS)
        self.assertEqual((closing.mind, closing.action), (SLOW, ("close", "this")))
        self.assertIn("not sure enough to close alone", closing.why)

    def test_apps_and_sites_it_knows_by_heart(self):
        self.assertEqual(self.minds.decide("open calculator", WINDOWS).action, ("open", "calculator"))
        self.assertEqual(self.minds.decide("open github.com", WINDOWS).mind, FAST)
        self.assertEqual(self.minds.decide("open sesame", WINDOWS).mind, SLOW)
        self.assertEqual(self.minds.decide("open readme.md", WINDOWS).mind, SLOW)

    def test_clicks_and_typing_are_never_fast(self):
        for request in ("click insert", "type hello"):
            self.assertEqual(self.minds.decide(request, WINDOWS).mind, SLOW, request)

    def test_confidence_is_never_certain_and_comes_from_the_dev_list(self):
        rows = fast_slow.score(fast_slow.DEV, self.minds)
        self.assertEqual(fast_slow.by_rule(rows), minds_module.PRIORS)
        for rule in minds_module.PRIORS:
            self.assertLess(self.minds.sure(rule), 1.0)
        self.assertEqual(sum(row["wrong_fast"] for row in rows), 0)


class PracticeTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.minds = Minds(None, clock=self.clock)

    def test_practice_turns_what_the_brain_does_into_a_habit(self):
        self.assertEqual(self.minds.decide("save it", WINDOWS).mind, SLOW)
        practise(self.minds, "save it", ("keys", "ctrl+s"), 7)
        self.assertEqual(self.minds.decide("save it", WINDOWS).mind, SLOW)
        practise(self.minds, "save it", ("keys", "ctrl+s"), 1)
        decision = self.minds.decide("Save it.", WINDOWS)
        self.assertEqual((decision.mind, decision.action), (FAST, ("keys", "ctrl+s")))

    def test_higher_stakes_need_more_practice(self):
        needed = fast_slow.practice_needed()
        self.assertLess(needed["focus"]["times"], needed["open"]["times"])
        self.assertLess(needed["open"]["times"], needed["keys"]["times"])
        self.assertLess(needed["keys"]["times"], needed["close"]["times"])
        practise(self.minds, "open spotify", ("open", "spotify"), needed["open"]["times"] - 1)
        self.assertEqual(self.minds.decide("open spotify", WINDOWS).mind, SLOW)
        practise(self.minds, "open spotify", ("open", "spotify"), 1)
        self.assertEqual(self.minds.decide("open spotify", WINDOWS).mind, FAST)

    def test_no_right_after_a_fast_action_makes_system_1_stand_down(self):
        practise(self.minds, "save it", ("keys", "ctrl+s"), 8)
        self.minds.acted(self.minds.decide("save it", WINDOWS))
        self.clock.now += 3
        self.minds.heard("no, not that")
        self.assertEqual(self.minds.decide("save it", WINDOWS).mind, SLOW)

    def test_a_no_long_after_is_not_about_that_action(self):
        practise(self.minds, "save it", ("keys", "ctrl+s"), 8)
        self.minds.acted(self.minds.decide("save it", WINDOWS))
        self.clock.now += minds_module.FEEDBACK_SECONDS + 1
        self.minds.heard("no")
        self.assertEqual(self.minds.decide("save it", WINDOWS).mind, FAST)

    def test_no_counts_against_a_built_in_rule_too(self):
        before = self.minds.sure("window")
        for _ in range(2):
            self.minds.acted(self.minds.decide("minimize notepad", WINDOWS))
            self.minds.heard("no")
        self.assertLess(self.minds.sure("window"), before)
        self.assertEqual(self.minds.decide("close notepad", WINDOWS).mind, SLOW)
        self.assertEqual(self.minds.decide("minimize notepad", WINDOWS).mind, FAST)

    def test_the_brain_changing_its_mind_counts_against_the_habit(self):
        practise(self.minds, "save it", ("keys", "ctrl+s"), 8)
        practise(self.minds, "save it", ("keys", "ctrl+shift+s"), 1)
        self.assertEqual(self.minds.decide("save it", WINDOWS).mind, SLOW)

    def test_what_must_never_become_a_habit(self):
        for phrase, actions in (("close it all", [("keys", "alt+f4")]), ("type hello", [("type", "hello")]),
                                ("click insert", [("click", "Insert")]), ("open the first one", [("open", "report.xlsx")]),
                                ("save it and close", [("keys", "ctrl+s")]),
                                ("open notepad", [("open", "notepad"), ("keys", "ctrl+n")])):
            practise(self.minds, phrase, actions[0], 0)
            self.minds.learn(phrase, actions)
            self.minds.heard("thanks")
            self.assertNotIn(minds_module.phrase_key(phrase), self.minds.counts, phrase)

    def test_a_corrected_first_try_is_forgotten(self):
        self.minds.learn("save it", [("keys", "ctrl+s")])
        self.minds.heard("no")
        self.assertEqual(self.minds.counts, {})

    def test_habits_survive_a_restart_and_the_file_does_not_hold_what_was_said(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "habits.json")
            practise(Minds(path), "save it", ("keys", "ctrl+s"), 8)
            saved = Path(path).read_text(encoding="utf-8")
            self.assertNotIn("save it", saved)
            self.assertEqual(sum(entry["right"] for entry in json.loads(saved)["counts"].values()), 8)
            self.assertEqual(Minds(path).decide("save it", WINDOWS).mind, FAST)

    def test_a_broken_file_starts_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "habits.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(Minds(str(path)).counts, {})


class FastPathTests(unittest.TestCase):
    """keyit_buddy.think_fast with a stand-in Buddy: no screen, no brain, no real window is touched."""

    def buddy(self, **settings):
        self.said, self.ran = [], []
        return types.SimpleNamespace(
            minds=Minds(None), settings={"fast_thinking": True, "buddy_speak": True, **settings}, windows=[],
            history=[], last_reply=None, last_timing=None, confirm=lambda question: False,
            speaker=types.SimpleNamespace(say=self.said.append), _stop_look_ahead=lambda: self.ran.append("stop look"),
            on_status=lambda text: None, post=lambda *args: None,
            overlay=types.SimpleNamespace(say=None, hide_later=None))

    def run_fast(self, buddy, question, selection=""):
        def run(action, argument, windows, targets=None, confirm=None, question=""):
            self.ran.append((action, argument, [w["app"] for w in windows], question))
            return "Done."

        with mock.patch.object(keyit_buddy.keyit_actions, "list_windows", return_value=list(WINDOWS)), \
                mock.patch.object(keyit_buddy.keyit_actions, "run", side_effect=run):
            return keyit_buddy.think_fast(buddy, question, selection)

    def test_a_routine_command_never_reaches_the_brain(self):
        buddy = self.buddy()
        self.assertTrue(self.run_fast(buddy, "close notepad"))
        self.assertEqual(self.ran, ["stop look", ("close", "this", ["Notepad"], "")])
        self.assertEqual(self.said, ["Done."])
        self.assertEqual(buddy.last_reply["mind"], FAST)
        self.assertEqual(buddy.history[-1], {"role": "assistant", "content": "Done."})

    def test_anything_else_goes_to_the_brain_untouched(self):
        buddy = self.buddy()
        for question in ("how do I close notepad", "close notepad and open excel", "what is this"):
            self.assertFalse(self.run_fast(buddy, question))
        self.assertFalse(self.run_fast(buddy, "close notepad", selection="highlighted"))
        self.assertEqual((self.ran, self.said, buddy.history), ([], [], []))

    def test_it_can_be_switched_off_and_still_hears_feedback(self):
        buddy = self.buddy(fast_thinking=False)
        buddy.minds.pending = ("window", None, None, buddy.minds.clock())
        self.assertFalse(self.run_fast(buddy, "no"))
        self.assertEqual(buddy.minds.counts["window"]["wrong"], 1)
        self.assertFalse(self.run_fast(buddy, "close notepad"))
        self.assertEqual(self.ran, [])

    def test_a_failing_action_is_said_not_raised(self):
        buddy = self.buddy()
        with mock.patch.object(keyit_buddy.keyit_actions, "list_windows", return_value=list(WINDOWS)), \
                mock.patch.object(keyit_buddy.keyit_actions, "run", side_effect=OSError("access denied")):
            self.assertTrue(keyit_buddy.think_fast(buddy, "minimize outlook"))
        self.assertEqual(self.said, ["I couldn't minimize that: access denied"])

    def test_the_brain_is_asked_only_when_system_1_passes(self):
        fake = types.SimpleNamespace()                       # no screen, no client: touching either would raise
        with mock.patch.object(keyit_buddy, "think_fast", return_value=True) as fast:
            keyit_buddy.Buddy._ask_locked(fake, "close notepad", 0)
        fast.assert_called_once_with(fake, "close notepad", "")


if __name__ == "__main__":
    unittest.main()
