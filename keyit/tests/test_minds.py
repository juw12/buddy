"""Two minds (keyit_minds): when System 1 acts alone, when it hands over, and how it learns.

    python -m pytest keyit/tests/test_minds.py -q
"""
import json
import re
import sys
import tempfile
import threading
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
        # without a trained model System 1 still counts as Buddy 1.5.0 did, and the counts are the DEV list's
        counted = Minds(None, model=None)
        rows = fast_slow.score(fast_slow.DEV, counted)
        self.assertEqual(fast_slow.by_rule(rows), minds_module.PRIORS)
        for rule in minds_module.PRIORS:
            self.assertLess(counted.sure(rule), 1.0)
        self.assertEqual(sum(row["wrong_fast"] for row in rows), 0)
        # with the trained model: never certain either, and still no wrong fast action on that list
        self.assertIsNotNone(self.minds.model)
        rows = fast_slow.score(fast_slow.DEV, self.minds)
        self.assertEqual(sum(row["wrong_fast"] for row in rows), 0)
        self.assertGreaterEqual(sum(row["mind"] == FAST for row in rows), 40)
        for row in rows:
            self.assertLessEqual(row["confidence"], minds_module.NEVER_CERTAIN, row["text"])
        for rule in minds_module.PRIORS:
            self.assertLess(self.minds.sure(rule), 1.0)


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


class TrainedTests(unittest.TestCase):
    """System 1 with the model that training on simulated use left behind (keyit/minds_model.json)."""

    def setUp(self):
        self.clock = Clock()
        self.minds = Minds(None, clock=self.clock)

    def test_the_model_file_is_what_training_gives(self):
        import minds_train

        shipped = json.loads(Path(minds_module.MODEL_FILE).read_text(encoding="utf-8"))
        fresh = minds_train.train(*minds_train.shipped_training_data())
        self.assertEqual(fresh["verbs"], shipped["verbs"])
        self.assertEqual(sorted(fresh["weights"]), sorted(shipped["weights"]))
        for name, weight in shipped["weights"].items():
            self.assertAlmostEqual(fresh["weights"][name], weight, delta=1e-3, msg=name)
        for rule, value in shipped["typical"].items():
            self.assertAlmostEqual(fresh["typical"][rule], value, delta=1e-3, msg=rule)
        for count in ("grammar_readings", "user_readings", "calibration_readings", "user_share"):
            self.assertEqual(fresh["training"][count], shipped["training"][count], count)

    def test_no_wrong_fast_action_on_the_test_list_of_buddy_1_5_0(self):
        rows = fast_slow.score(fast_slow.TEST, self.minds)       # first scored after the model was frozen
        self.assertEqual([row["text"] for row in rows if row["wrong_fast"]], [])
        self.assertGreaterEqual(sum(row["mind"] == FAST for row in rows), 40)

    def test_the_words_around_a_command_come_off(self):
        plain = minds_module.plain
        self.assertEqual(plain("Hey Buddy, could you close Notepad for me?"),
                         ("close Notepad", {"opener", "frame", "asked", "closer"}))
        self.assertEqual(plain("close excel?"), ("close excel?", set()))        # no "can you": still a doubt
        self.assertEqual(plain("please"), ("please", set()))                    # never down to nothing
        self.assertEqual(plain("minimize this window please."), ("minimize this window", {"closer"}))

    def test_a_polite_command_is_read_like_the_bare_one(self):
        bare = self.minds.decide("close notepad", WINDOWS)
        self.assertEqual(bare.mind, FAST)
        for request in ("can you close notepad", "Could you close Notepad, please?", "hey buddy, close notepad for me",
                        "I want to close notepad"):
            polite = self.minds.decide(request, WINDOWS)
            self.assertEqual((polite.mind, polite.action[0], polite.window["app"]), (FAST, "close", "Notepad"), request)
            self.assertAlmostEqual(polite.confidence, bare.confidence, places=9, msg=request)
        for request, why in (("close excel?", "a doubt"), ("could you maybe close notepad", "a doubt"),
                             ("can you close notepad and excel", "two steps"),
                             ("please don't close notepad", "a negation")):
            decision = self.minds.decide(request, WINDOWS)
            self.assertEqual((decision.mind, decision.why), (SLOW, why), request)

    def test_wordings_with_the_window_in_the_middle(self):
        for request, action, app in (("make excel full screen", "maximize", "Excel"),
                                     ("put notepad away", "minimize", "Notepad"),
                                     ("bring outlook to the front", "focus", "Outlook")):
            decision = self.minds.decide(request, WINDOWS)
            self.assertEqual((decision.mind, decision.action[0], decision.window["app"]), (FAST, action, app), request)

    def test_it_and_that_need_a_look(self):
        for request in ("close it", "minimize that", "maximize it"):
            decision = self.minds.decide(request, WINDOWS)
            self.assertEqual((decision.mind, decision.action), (SLOW, None), request)

    def test_a_wording_training_never_saw_is_not_guessed_at(self):
        self.assertNotIn("stop", self.minds.model.verbs)
        self.assertEqual(self.minds.decide("stop notepad", WINDOWS).mind, SLOW)
        model = minds_module.Model({"verbs": {"zap": "close"}, "weights": {"bias": 9.0}, "typical": {}})
        decision = Minds(None, model=model).decide("zap notepad", WINDOWS)      # sure of everything, taught nothing
        self.assertEqual((decision.mind, decision.action), (SLOW, None))

    def test_opening_what_is_already_open_is_left_to_system_2(self):
        self.assertEqual(self.minds.decide("open calculator", WINDOWS).mind, FAST)
        decision = self.minds.decide("open notepad", WINDOWS)                   # switch to it, or a second one?
        self.assertEqual((decision.mind, decision.action), (SLOW, ("open", "notepad")))
        self.assertLess(decision.confidence, minds_module.STAKES["open"])

    def test_without_a_usable_model_file_system_1_is_buddy_1_5_0(self):
        with tempfile.TemporaryDirectory() as folder:
            missing, broken, wrong = (Path(folder) / name for name in ("missing.json", "broken.json", "wrong.json"))
            broken.write_text("{not json", encoding="utf-8")
            wrong.write_text(json.dumps({"verbs": {"close": "format the disk"}, "weights": {"bias": 1.0},
                                         "typical": {}}), encoding="utf-8")
            for path in (missing, broken, wrong):
                with self.assertLogs(level="WARNING"):
                    self.assertIsNone(minds_module.load_model(str(path)), path.name)
        counted = Minds(None, model=None)
        decision = counted.decide("close notepad", WINDOWS)
        self.assertEqual((decision.mind, decision.confidence), (FAST, minds_module.confidence(22, 22)))
        self.assertEqual(counted.decide("can you close notepad", WINDOWS).why, "a doubt")

    def test_the_model_file_ships_with_the_app(self):
        self.assertIsNotNone(minds_module.load_model())
        root = Path(__file__).resolve().parents[2]
        copies = [line.strip() for line in (root / "build.bat").read_text(encoding="utf-8").splitlines()
                  if line.strip().startswith("robocopy keyit ")]
        self.assertEqual(len(copies), 1)
        self.assertIn(" /e ", copies[0])                                        # every file in keyit/, not only *.py
        self.assertNotIn("/xf", copies[0])

    def test_a_no_takes_trust_away_and_practice_never_adds_more_than_was_trained(self):
        trained = self.minds.decide("minimize notepad", WINDOWS).confidence
        for _ in range(30):                                                     # thirty times right
            self.minds.acted(self.minds.decide("minimize notepad", WINDOWS))
            self.minds.heard("what is on my screen")
        self.assertEqual(self.minds.decide("minimize notepad", WINDOWS).confidence, trained)
        self.minds.acted(self.minds.decide("minimize notepad", WINDOWS))
        self.clock.now += 3
        self.minds.heard("no")
        after = self.minds.decide("minimize notepad", WINDOWS).confidence
        self.assertLess(after, trained)
        self.assertEqual(self.minds.decide("close notepad", WINDOWS).mind, SLOW)   # same rule, higher stakes
        self.minds.acted(self.minds.decide("minimize notepad", WINDOWS))
        self.clock.now += minds_module.FEEDBACK_SECONDS + 1
        self.minds.heard("no")                                                  # too late to be about it
        self.assertGreater(self.minds.decide("minimize notepad", WINDOWS).confidence, after)

    def test_feedback_from_another_thread_waits_its_turn(self):
        held, settle = [], self.minds._settle

        def watched(question):
            got = []

            def try_it():
                got.append(self.minds.lock.acquire(timeout=0.05))
                if got[0]:
                    self.minds.lock.release()

            other = threading.Thread(target=try_it)
            other.start()
            other.join()
            held.append(not got[0])
            return settle(question)

        self.minds._settle = watched
        self.minds.acted(self.minds.decide("minimize notepad", WINDOWS))
        self.minds.heard("no")
        self.assertEqual(held, [True])

        self.minds._settle = settle
        decision = self.minds.decide("minimize notepad", WINDOWS)

        def many():
            for _ in range(300):
                self.minds.acted(decision)
                self.minds.heard("no")

        threads = [threading.Thread(target=many) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        entry = self.minds.counts["window"]
        self.assertLessEqual(entry["right"] + entry["wrong"], 1 + 6 * 300)
        self.assertGreater(entry["wrong"], 0)


class TalkFeedbackTests(unittest.TestCase):
    """Talk mode: a "no" said over Buddy's voice reaches System 1 through Buddy._corrected."""

    def said_over_buddy(self, said):
        """(confidence before, confidence after, is the action still waiting for a verdict)"""
        clock = Clock()
        minds = Minds(None, clock=clock)
        before = minds.decide("minimize notepad", WINDOWS)
        minds.acted(before)
        clock.now += 2
        keyit_buddy.Buddy._corrected(types.SimpleNamespace(minds=minds), said)
        return before.confidence, minds.decide("minimize notepad", WINDOWS).confidence, minds.pending is not None

    def check_no_lowers_the_confidence(self):
        for said in ("No.", "no, not that", "Nope"):
            before, after, waiting = self.said_over_buddy(said)
            self.assertLess(after, before, said)
            self.assertFalse(waiting, said)

    def test_no_said_over_buddy_counts_against_the_fast_action(self):
        self.check_no_lowers_the_confidence()

    def test_stop_and_wait_only_stop_the_voice(self):
        for said in ("Stop.", "wait", "hold on", "quiet"):
            before, after, waiting = self.said_over_buddy(said)
            self.assertEqual(after, before, said)
            self.assertTrue(waiting, said)                  # a "no" that follows is still about that action

    def test_buddy_hands_talk_mode_its_corrected(self):
        import inspect

        self.assertIn("corrected=self._corrected", inspect.getsource(keyit_buddy.Buddy))
        keyit_buddy.Buddy._corrected(types.SimpleNamespace(), "no")             # a Buddy without minds: no error

    def test_these_checks_fail_when_the_feedback_is_broken_on_purpose(self):
        faults = (mock.patch.object(minds_module, "CORRECTIONS", re.compile(r"never\b^")),      # no "no" is heard
                  mock.patch.object(Minds, "_settle", lambda self, question: None),             # nothing is counted
                  mock.patch.object(Minds, "trust", lambda self, rule: 1.0),                    # counted, not used
                  mock.patch.object(keyit_buddy.Buddy, "_corrected", lambda self, text: None))  # talk mode not wired
        for fault in faults:
            with fault, self.assertRaises(AssertionError):
                self.check_no_lowers_the_confidence()
        typed = PracticeTests("test_no_counts_against_a_built_in_rule_too")
        for fault in faults[:3]:
            typed.setUp()
            with fault, self.assertRaises(AssertionError):
                typed.test_no_counts_against_a_built_in_rule_too()
        typed.setUp()
        typed.test_no_counts_against_a_built_in_rule_too()                      # and pass when nothing is broken


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
