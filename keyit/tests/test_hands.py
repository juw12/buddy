"""Crew hands (crew_hands.py): agents that really click and type. No window is touched here: the screen, the
user and the clock are stand-ins."""

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import crew_hands  # noqa: E402
import crew_sources  # noqa: E402
from crew_hands import Hands, normalize_request  # noqa: E402


def thing(name, kind="button", rect=(100, 200, 300, 260), visible=True):
    return {"name": name, "kind": kind, "rect": rect, "hwnd": 7, "foreground": False, "visible": visible,
            "element": object()}


class FakeScreen:
    """One window, "Notepad", with the given things in it. Physical pixels are twice the crew's own pixels."""

    def __init__(self, things=(), pressable=True, takes_text=True):
        self.things = list(things)
        self.pressable = pressable
        self.takes_text = takes_text
        self.gone = False                     # the thing moved or got covered after it was found
        self.buddy_at = False
        self.name_under_point = ""
        self.log = []

    def find_window(self, needle):
        return (7, "notes.txt - Notepad") if needle.lower() in "notes.txt - notepad" else None

    def elements(self, hwnd):
        return list(self.things)

    def still_visible(self, item):
        return not self.gone

    def press(self, item):
        self.log.append(("press", item["name"]))
        return self.pressable

    def set_text(self, item, text):
        self.log.append(("set_text", item["name"], text))
        return self.takes_text

    def mouse_click(self, x, y, then_type=None):
        self.log.append(("mouse", x, y, then_type))

    def name_at(self, x, y):
        return self.name_under_point

    def is_buddy_at(self, x, y):
        return self.buddy_at

    def to_own(self, x, y):
        return int(x / 2), int(y / 2)

    def to_physical(self, x, y):
        return x * 2, y * 2


class HandsCase(unittest.TestCase):
    def setUp(self):
        self.shown = []                       # what the crew was told, in order
        self.steps = []                       # shows, sleeps and the screen's actions, in order
        self.answers = {}
        self.questions = []
        self.say_yes = True
        self.allowed = True
        self.now = 5000.0

    def hands(self, screen, confirm=True):
        def show(event):
            self.shown.append(event)
            self.steps.append(("show", event.get("say")))

        def ask_user(question):
            self.questions.append(question)
            return self.say_yes

        screen.log = self.steps
        return Hands(show, screen=screen, allowed=lambda request: self.allowed,
                     confirm=ask_user if confirm else None,
                     reply=lambda request_id, result: self.answers.__setitem__(request_id, result),
                     clock=lambda: self.now, sleep=lambda seconds: self.steps.append(("sleep", seconds)))

    @staticmethod
    def request(**fields):
        return normalize_request(dict({"agent": "tester", "do": "click", "window": "Notepad"}, **fields))


class NormalizeTests(unittest.TestCase):
    def test_only_an_event_with_a_known_do_is_a_request(self):
        self.assertIsNone(normalize_request({"agent": "tester", "say": "Reading"}))
        self.assertIsNone(normalize_request({"agent": "tester", "do": "format the disk"}))
        self.assertIsNone(normalize_request({"do": "click", "target": "Save"}))
        self.assertIsNone(normalize_request("tester: click"))

    def test_a_request_keeps_who_asked_so_the_agents_own_pointer_clicks(self):
        request = normalize_request({"agent": "tester", "team": "OpenCode", "id": "opencode:ses_1",
                                     "color": "#22c55e", "do": "click", "target": "Save", "window": "Notepad"})
        self.assertEqual(request["who"], {"agent": "tester", "team": "OpenCode", "id": "opencode:ses_1",
                                          "color": "#22c55e"})
        self.assertEqual(request["key"], "opencode:ses_1")
        self.assertEqual((request["target"], request["window"]), ("Save", "Notepad"))

    def test_the_request_id_can_only_be_a_plain_file_name(self):
        self.assertEqual(normalize_request({"agent": "a", "do": "look", "request": "abc-123_X"})["request"], "abc-123_X")
        for bad in ("..\\..\\evil", "a/b", "", 7, None, "x" * 65):
            self.assertEqual(normalize_request({"agent": "a", "do": "look", "request": bad})["request"], "")

    def test_long_text_is_cut(self):
        request = normalize_request({"agent": "a", "do": "type", "text": "x" * 5000})
        self.assertEqual(len(request["text"]), crew_hands.TEXT_CHARS)


class ClickTests(HandsCase):
    def test_the_pointer_is_at_the_button_before_it_is_pressed(self):
        hands = self.hands(FakeScreen([thing("Save")]))
        result = hands.do(self.request(target="Save"))
        self.assertEqual(result, {"ok": True, "did": "Clicked Save", "how": "direct"})
        self.assertEqual(self.steps, [("show", "Clicking Save"), ("sleep", crew_hands.ARRIVE_S),
                                      ("press", "Save"), ("show", "Clicked Save \u2713")])
        # the centre of (100, 200, 300, 260) in physical pixels is (200, 230): half of that in the crew's own
        self.assertEqual((self.shown[0]["x"], self.shown[0]["y"]), (100, 115))
        self.assertEqual(self.shown[0]["agent"], "tester")

    def test_a_press_needs_no_mouse(self):
        hands = self.hands(FakeScreen([thing("Save")]))
        hands.do(self.request(target="Save"))
        self.assertFalse([step for step in self.steps if step[0] == "mouse"])

    def test_the_real_mouse_is_used_where_the_program_offers_no_other_way(self):
        hands = self.hands(FakeScreen([thing("Save")], pressable=False))
        result = hands.do(self.request(target="Save"))
        self.assertEqual(result["how"], "mouse")
        self.assertIn(("mouse", 200.0, 230.0, None), self.steps)

    def test_what_is_covered_by_another_window_is_left_alone(self):
        hands = self.hands(FakeScreen([thing("Save", visible=False)]))
        result = hands.do(self.request(target="Save"))
        self.assertFalse(result["ok"])
        self.assertIn("covered", result["did"])
        self.assertEqual(self.steps, [])                   # no pointer, no press

    def test_something_that_moved_before_the_click_is_not_clicked(self):
        screen = FakeScreen([thing("Save")])
        screen.gone = True
        result = self.hands(screen).do(self.request(target="Save"))
        self.assertFalse(result["ok"])
        self.assertFalse([step for step in self.steps if step[0] in ("press", "mouse")])

    def test_a_window_that_is_not_on_screen(self):
        result = self.hands(FakeScreen([thing("Save")])).do(self.request(target="Save", window="Excel"))
        self.assertEqual(result, {"ok": False, "did": "No window called Excel is on screen."})

    def test_a_window_has_to_be_named(self):
        result = self.hands(FakeScreen([thing("Save")])).do(self.request(target="Save", window=""))
        self.assertFalse(result["ok"])
        self.assertIn("window", result["did"])

    def test_a_name_that_is_not_there(self):
        result = self.hands(FakeScreen([thing("Save")])).do(self.request(target="Print"))
        self.assertEqual(result, {"ok": False, "did": "There is no Print in Notepad."})

    def test_dont_save_is_never_taken_for_save(self):
        hands = self.hands(FakeScreen([thing("Don't Save")]))
        self.assertFalse(hands.do(self.request(target="Save"))["ok"])
        self.assertEqual(self.steps, [])

    def test_a_click_needs_a_name_or_a_point(self):
        result = self.hands(FakeScreen()).do(self.request())
        self.assertFalse(result["ok"])

    def test_a_windows_close_button_is_not_for_agents(self):
        hands = self.hands(FakeScreen([thing("Close")]))
        result = hands.do(self.request(target="Close"))
        self.assertFalse(result["ok"])
        self.assertEqual(self.steps, [])


class RulesTests(HandsCase):
    def test_nothing_is_done_before_the_user_said_yes_to_agents_that_click(self):
        self.allowed = False
        result = self.hands(FakeScreen([thing("Save")])).do(self.request(target="Save"))
        self.assertEqual(result, {"ok": False, "did": crew_hands.NOT_ALLOWED})
        self.assertEqual(self.steps, [])

    def test_looking_needs_that_yes_too(self):
        self.allowed = False
        result = self.hands(FakeScreen([thing("Save")])).do(self.request(do="look"))
        self.assertFalse(result["ok"])

    def test_a_request_that_waited_too_long_is_not_done(self):
        hands = self.hands(FakeScreen([thing("Save")]))
        result = hands.do(self.request(target="Save", at=self.now - crew_hands.REQUEST_MAX_AGE_S - 1))
        self.assertFalse(result["ok"])
        self.assertEqual(self.steps, [])
        self.assertTrue(hands.do(self.request(target="Save", at=self.now - 5))["ok"])

    def test_a_risky_click_is_asked_about_first_and_done_after_a_yes(self):
        hands = self.hands(FakeScreen([thing("Delete all")]))
        result = hands.do(self.request(target="Delete all"))
        self.assertTrue(result["ok"])
        self.assertEqual(len(self.questions), 1)
        self.assertIn('tester wants to click "Delete all"', self.questions[0])
        self.assertEqual(self.shown[0]["state"], "waiting")       # the pointer waits at the button for you
        self.assertEqual(self.shown[0]["say"], 'May I click "Delete all"?')
        self.assertIn(("press", "Delete all"), self.steps)

    def test_a_risky_click_is_not_done_after_a_no(self):
        self.say_yes = False
        hands = self.hands(FakeScreen([thing("Send")]))
        result = hands.do(self.request(target="Send"))
        self.assertFalse(result["ok"])
        self.assertFalse([step for step in self.steps if step[0] in ("press", "mouse")])
        self.assertEqual(self.shown[-1]["state"], "working")      # it no longer says it needs you

    def test_a_risky_click_is_not_done_when_nobody_can_be_asked(self):
        hands = self.hands(FakeScreen([thing("Send")]), confirm=False)
        self.assertFalse(hands.do(self.request(target="Send"))["ok"])
        self.assertFalse([step for step in self.steps if step[0] in ("press", "mouse")])

    def test_an_ordinary_click_asks_nobody(self):
        self.hands(FakeScreen([thing("Save")])).do(self.request(target="Save"))
        self.assertEqual(self.questions, [])


class PointTests(HandsCase):
    def test_a_point_is_clicked_with_the_real_mouse_in_physical_pixels(self):
        result = self.hands(FakeScreen()).do(self.request(x=450, y=250, window=""))
        self.assertEqual(result, {"ok": True, "did": "Clicked at 450, 250", "how": "mouse"})
        self.assertEqual(self.steps, [("show", "Clicking here"), ("sleep", crew_hands.ARRIVE_S),
                                      ("mouse", 900, 500, None), ("show", "Clicked \u2713")])
        self.assertEqual((self.shown[0]["x"], self.shown[0]["y"]), (450, 250))

    def test_buddys_own_windows_are_never_clicked(self):
        screen = FakeScreen()
        screen.buddy_at = True
        result = self.hands(screen).do(self.request(x=450, y=250))
        self.assertFalse(result["ok"])
        self.assertEqual(self.steps, [])

    def test_the_rules_go_by_what_is_at_the_point(self):
        screen = FakeScreen()
        screen.name_under_point = "Close"
        self.assertFalse(self.hands(screen).do(self.request(x=450, y=250))["ok"])
        screen.name_under_point = "Delete"
        self.say_yes = False
        self.assertFalse(self.hands(screen).do(self.request(x=450, y=250))["ok"])
        self.assertEqual(len(self.questions), 1)
        self.assertFalse([step for step in self.steps if step[0] == "mouse"])


class TypeTests(HandsCase):
    def test_text_goes_into_the_box_without_the_keyboard(self):
        hands = self.hands(FakeScreen([thing("File name", kind="text box")]))
        result = hands.do(self.request(do="type", target="File name", text="notes"))
        self.assertEqual(result, {"ok": True, "did": "Typed in File name", "how": "direct"})
        self.assertIn(("set_text", "File name", "notes"), self.steps)
        self.assertFalse([step for step in self.steps if step[0] == "mouse"])

    def test_the_keyboard_is_used_where_the_program_offers_no_other_way(self):
        hands = self.hands(FakeScreen([thing("File name", kind="text box")], takes_text=False))
        result = hands.do(self.request(do="type", target="File name", text="notes"))
        self.assertEqual(result["how"], "keyboard")
        self.assertIn(("mouse", 200.0, 230.0, "notes"), self.steps)

    def test_what_is_typed_is_never_in_the_bubble_or_the_answer(self):
        hands = self.hands(FakeScreen([thing("Password", kind="text box")]))
        result = hands.do(self.request(do="type", target="Password", text="hunter2-secret"))
        self.assertNotIn("hunter2", json.dumps(self.shown) + json.dumps(result))

    def test_typing_needs_a_box_and_a_text(self):
        hands = self.hands(FakeScreen([thing("File name", kind="text box")]))
        self.assertFalse(hands.do(self.request(do="type", target="File name"))["ok"])
        self.assertFalse(hands.do(self.request(do="type", text="notes"))["ok"])
        self.assertEqual(self.steps, [])


class PickTests(HandsCase):
    def test_a_button_called_just_open_can_be_clicked(self):
        screen = FakeScreen([thing("Open file location"), thing("Open")])
        self.assertEqual(self.hands(screen).do(self.request(target="Open"))["did"], "Clicked Open")

    def test_the_exact_name_wins_over_a_longer_one(self):
        screen = FakeScreen([thing("Save as"), thing("Save", kind="text"), thing("Save")])
        self.hands(screen).do(self.request(target="save"))
        self.assertIn(("press", "Save"), self.steps)

    def test_dont_save_is_never_taken_for_save(self):
        screen = FakeScreen([thing("Don't Save")])
        self.assertFalse(self.hands(screen).do(self.request(target="Save"))["ok"])


class LookTests(HandsCase):
    def test_a_look_lists_what_can_be_seen_with_points_the_agent_can_click(self):
        screen = FakeScreen([thing("Save"), thing("Hidden", visible=False),
                             thing("File name", kind="text box", rect=(400, 100, 800, 140))])
        result = self.hands(screen).do(self.request(do="look"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["window"], "notes.txt - Notepad")
        self.assertEqual(result["elements"], [{"name": "Save", "kind": "button", "x": 100, "y": 115},
                                              {"name": "File name", "kind": "text box", "x": 300, "y": 60}])
        self.assertEqual(self.shown[0]["window"], "Notepad")      # the pointer goes to that window

    def test_a_long_list_is_cut_and_says_so(self):
        screen = FakeScreen([thing(f"Item {number}", kind="list item") for number in range(crew_hands.LOOK_MAX + 7)])
        result = self.hands(screen).do(self.request(do="look"))
        self.assertEqual(len(result["elements"]), crew_hands.LOOK_MAX)
        self.assertEqual(result["more"], 7)


class ManyAtOnceTests(HandsCase):
    def wait_for(self, done, seconds=5.0):
        deadline = time.monotonic() + seconds
        while not done() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(done())

    def test_two_agents_press_in_the_same_moment(self):
        together = threading.Barrier(2)

        class SlowScreen(FakeScreen):
            def press(self, item):
                together.wait(timeout=5)              # passes only when both agents are in here at once
                return True

        hands = self.hands(SlowScreen([thing("Save"), thing("Print")]))
        self.assertTrue(hands.submit({"agent": "one", "do": "click", "target": "Save", "window": "Notepad",
                                      "request": "r1"}))
        self.assertTrue(hands.submit({"agent": "two", "do": "click", "target": "Print", "window": "Notepad",
                                      "request": "r2"}))
        self.wait_for(lambda: len(self.answers) == 2)
        self.assertEqual(self.answers["r1"]["did"], "Clicked Save")
        self.assertEqual(self.answers["r2"]["did"], "Clicked Print")
        self.assertEqual({event["agent"] for event in self.shown}, {"one", "two"})

    def test_one_agents_requests_are_done_in_the_order_they_came(self):
        hands = self.hands(FakeScreen([thing("Save"), thing("Print"), thing("Open")]))
        for number, name in enumerate(("Save", "Print", "Open")):
            hands.submit({"agent": "one", "do": "click", "target": name, "window": "Notepad",
                          "request": f"r{number}"})
        self.wait_for(lambda: len(self.answers) == 3)
        self.assertEqual([step[1] for step in self.steps if step[0] == "press"], ["Save", "Print", "Open"])

    def test_an_event_that_asks_for_nothing_is_not_taken(self):
        hands = self.hands(FakeScreen())
        self.assertFalse(hands.submit({"agent": "one", "say": "Reading README.md"}))
        self.assertEqual(hands.busy(), 0)

    def test_more_agents_than_the_crew_holds_are_told_to_wait(self):
        release = threading.Event()

        class StuckScreen(FakeScreen):
            def press(self, item):
                release.wait(5)
                return True

        hands = self.hands(StuckScreen([thing("Save")]))
        for number in range(crew_hands.MAX_AGENTS + 1):
            hands.submit({"agent": f"agent{number}", "do": "click", "target": "Save", "window": "Notepad",
                          "request": f"r{number}"})
        self.wait_for(lambda: f"r{crew_hands.MAX_AGENTS}" in self.answers)
        self.assertFalse(self.answers[f"r{crew_hands.MAX_AGENTS}"]["ok"])
        release.set()
        self.wait_for(lambda: len(self.answers) == crew_hands.MAX_AGENTS + 1)
        self.assertEqual(sum(answer["ok"] for answer in self.answers.values()), crew_hands.MAX_AGENTS)

    def test_a_failure_in_one_request_is_answered_and_the_agent_goes_on(self):
        class BrokenOnce(FakeScreen):
            def elements(self, hwnd):
                if not self.log:
                    self.log.append(("broke",))
                    raise OSError("the window froze")
                return super().elements(hwnd)

        hands = self.hands(BrokenOnce([thing("Save")]))
        hands.submit({"agent": "one", "do": "click", "target": "Save", "window": "Notepad", "request": "r1"})
        hands.submit({"agent": "one", "do": "click", "target": "Save", "window": "Notepad", "request": "r2"})
        self.wait_for(lambda: len(self.answers) == 2)
        self.assertFalse(self.answers["r1"]["ok"])
        self.assertTrue(self.answers["r2"]["ok"])

    def test_after_close_nothing_is_taken(self):
        hands = self.hands(FakeScreen([thing("Save")]))
        hands.close()
        hands.submit({"agent": "one", "do": "click", "target": "Save", "window": "Notepad", "request": "r1"})
        self.assertFalse(self.answers["r1"]["ok"])
        self.assertEqual(self.steps, [])


class WatcherTests(unittest.TestCase):
    """Which events reach the hands (crew_sources.CrewWatcher)."""

    class Crew:
        def __init__(self):
            self.posted = []

        def post(self, event):
            self.posted.append(event)

        def tick(self):
            pass

    class TakesRequests:
        def __init__(self):
            self.taken = []

        def submit(self, event):
            if event.get("do"):
                self.taken.append(event)
                return True
            return False

    class OtherSource:
        def __init__(self, events):
            self.events = events

        def poll(self):
            events, self.events = self.events, []
            return events

    def test_a_do_line_in_the_inbox_goes_to_the_hands_and_the_rest_to_the_crew(self):
        with tempfile.TemporaryDirectory() as folder:
            inbox = Path(folder) / "inbox.jsonl"
            source = crew_sources.InboxSource(inbox)
            crew, hands = self.Crew(), self.TakesRequests()
            watcher = crew_sources.CrewWatcher(crew, sources=[source], hands=hands)
            crew_sources.post_event({"agent": "one", "do": "click", "target": "Save", "window": "Notepad"}, inbox)
            crew_sources.post_event({"agent": "two", "say": "Reading README.md"}, inbox)
            watcher.step()
        self.assertEqual([event["agent"] for event in hands.taken], ["one"])
        self.assertEqual([event["agent"] for event in crew.posted], ["two"])

    def test_what_an_agent_read_or_wrote_can_never_ask_for_a_click(self):
        crew, hands = self.Crew(), self.TakesRequests()
        source = self.OtherSource([{"agent": "one", "do": "click", "target": "Save", "window": "Notepad"}])
        crew_sources.CrewWatcher(crew, sources=[source], hands=hands).step()
        self.assertEqual(hands.taken, [])
        self.assertEqual(len(crew.posted), 1)

    def test_without_hands_everything_goes_to_the_crew(self):
        with tempfile.TemporaryDirectory() as folder:
            inbox = Path(folder) / "inbox.jsonl"
            source = crew_sources.InboxSource(inbox)
            crew = self.Crew()
            crew_sources.post_event({"agent": "one", "do": "click", "target": "Save"}, inbox)
            crew_sources.CrewWatcher(crew, sources=[source]).step()
        self.assertEqual(len(crew.posted), 1)


class AnswerTests(unittest.TestCase):
    """How `Buddy.exe --crew` gets its answer (ask, write_result)."""

    def test_ask_posts_the_request_and_returns_the_answer_buddy_left(self):
        with tempfile.TemporaryDirectory() as folder:
            posted = []

            def post(event):
                posted.append(event)
                crew_hands.write_result(event["request"], {"ok": True, "did": "Clicked Save"}, folder)

            result = crew_hands.ask({"agent": "one", "do": "click", "target": "Save"}, post=post, folder=folder)
            self.assertEqual(result, {"ok": True, "did": "Clicked Save"})
            self.assertEqual(posted[0]["agent"], "one")
            self.assertRegex(posted[0]["request"], r"^[0-9a-f]{32}$")
            self.assertAlmostEqual(posted[0]["at"], time.time(), delta=5)
            self.assertEqual(list(Path(folder).iterdir()), [])          # the answer was fetched and removed

    def test_ask_gives_up_when_buddy_does_not_answer(self):
        with tempfile.TemporaryDirectory() as folder:
            clock = iter(range(1000))
            result = crew_hands.ask({"agent": "one", "do": "look"}, wait_s=3, post=lambda event: None,
                                    folder=folder, sleep=lambda seconds: None, clock=lambda: next(clock))
        self.assertFalse(result["ok"])
        self.assertIn("did not answer", result["did"])

    def test_a_request_without_an_id_gets_no_answer_file(self):
        with tempfile.TemporaryDirectory() as folder:
            crew_hands.write_result("", {"ok": True}, folder)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_answers_nobody_fetched_are_thrown_away(self):
        with tempfile.TemporaryDirectory() as folder:
            crew_hands.write_result("old", {"ok": True}, folder)
            crew_hands.write_result("new", {"ok": True}, folder)
            now = time.time()
            crew_hands.tidy_results(folder, now=now + crew_hands.RESULT_KEEP_S + 1)
            self.assertEqual(list(Path(folder).iterdir()), [])
            crew_hands.write_result("new", {"ok": True}, folder)
            crew_hands.tidy_results(folder, now=now)
            self.assertEqual([path.name for path in Path(folder).iterdir()], ["new.json"])

    def test_the_crew_line_of_the_command_takes_a_do(self):
        event = crew_sources.event_from_text('{"agent": "one", "do": "click", "target": "Save", "window": "Notepad"}')
        self.assertEqual(normalize_request(event)["target"], "Save")


class PixelTests(unittest.TestCase):
    def test_a_point_keeps_its_place_on_a_monitor_measured_in_other_pixels(self):
        physical, own = (0, 0, 2560, 1600), (0, 0, 1280, 800)
        self.assertEqual(crew_hands.scaled((1800, 1000), physical, own), (900, 500))
        self.assertEqual(crew_hands.scaled((900, 500), own, physical), (1800, 1000))

    def test_a_second_monitor_to_the_left(self):
        physical, own = (-1920, 0, 0, 1080), (-960, 0, 0, 540)
        self.assertEqual(crew_hands.scaled((-960, 540), physical, own), (-480, 270))

    def test_a_monitor_without_a_size_changes_nothing(self):
        self.assertEqual(crew_hands.scaled((10, 20), (0, 0, 0, 0), (0, 0, 100, 100)), (10, 20))


if __name__ == "__main__":
    unittest.main()
