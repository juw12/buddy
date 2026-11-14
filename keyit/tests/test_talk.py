"""Talk mode (keyit_talk): turn taking, barge-in, turn repair - with fakes, no microphone or model.

    python -m pytest keyit/tests/test_talk.py -q
"""
import sys
import threading
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import keyit_talk as talk  # noqa: E402

RATE = talk.RATE
STEP = 0.03


class FakeListening:
    """Loudness instead of Silero: speech is anything above 0.01, a stretch ends after 0.3 s of quiet."""

    WINDOW = 160

    def __init__(self):
        self.heard = 0
        self.start = None
        self.quiet = 0
        self.left = np.zeros(0, dtype=np.float32)

    @property
    def since(self):
        return self.start

    def hear(self, audio):
        audio = np.concatenate([self.left, audio])
        whole = len(audio) - len(audio) % self.WINDOW
        found = []
        for index in range(0, whole, self.WINDOW):
            if np.abs(audio[index:index + self.WINDOW]).mean() > 0.01:
                if self.start is None:
                    self.start = self.heard
                self.quiet = 0
            elif self.start is not None:
                self.quiet += self.WINDOW
                if self.quiet >= 0.3 * RATE:
                    found.append((self.start, self.heard + self.WINDOW - self.quiet))
                    self.start = None
            self.heard += self.WINDOW
        self.left = audio[whole:]
        return found


def voice(seconds, seed, level=0.1):
    """Noise with four syllables a second: loud and soft like speech, at about `level` RMS."""
    random = np.random.default_rng(seed)
    at = np.arange(int(seconds * RATE)) / RATE
    envelope = 0.25 + 0.75 * 0.5 * (1 - np.cos(2 * np.pi * 4 * at))
    return (random.standard_normal(len(at)) * envelope * level * 1.5).astype(np.float32)


def quiet(seconds, seed=9):
    return (np.random.default_rng(seed).standard_normal(int(seconds * RATE)) * 0.0005).astype(np.float32)


def steps(audio):
    size = int(STEP * RATE)
    return [audio[start:start + size] for start in range(0, len(audio), size)]


class Completeness(unittest.TestCase):
    def test_finished_questions_and_commands(self):
        for text in ("What is on my screen?", "how do i change the font size", "Open the file menu.",
                     "can you explain this error", "tolong buka pengaturan", "這個按鈕是做什麼的",
                     "请帮我打开设置", "show me that again please"):
            self.assertEqual(talk.completeness(text), talk.COMPLETE, text)

    def test_unfinished_turns_wait(self):
        for text in ("open the file and", "I want to change the", "so I was thinking that maybe, ",
                     "click on settings then go to", "what is", "um", "the problem is that it's",
                     "saya mau buka file yang", "我想要打開桌面上的", "先打开设置然后", "how do I..."):
            self.assertEqual(talk.completeness(text), talk.UNFINISHED, text)

    def test_the_rest_gets_the_plain_gap(self):
        for text in ("The second one.", "what is this for", "I think so", "I don't know where it is",
                     "Das ist ein Test", "これは何ですか"):
            self.assertEqual(talk.completeness(text), talk.NEUTRAL, text)
        for text in ("", "  ", "...", "?!"):
            self.assertEqual(talk.completeness(text), talk.EMPTY, repr(text))

    def test_what_was_said(self):
        kinds = {"Goodbye!": talk.GOODBYE, "stop listening": talk.GOODBYE, "再見": talk.GOODBYE,
                 "Wait.": talk.INTERRUPT, "no": talk.INTERRUPT, "等一下": talk.INTERRUPT,
                 "Mm-hm.": talk.NOISE, "uh huh": talk.NOISE, "嗯": talk.NOISE,
                 "Yeah.": talk.BACKCHANNEL, "ok ok": talk.BACKCHANNEL, "I see": talk.BACKCHANNEL,
                 "好的": talk.BACKCHANNEL, "wait, which one?": talk.SPEECH, "yes open that one": talk.SPEECH}
        for text, kind in kinds.items():
            self.assertEqual(talk.spoken_kind(text), kind, text)

    def test_own_words(self):
        said = ["Click File in the top left corner.", "Then choose Save As."]
        self.assertTrue(talk.own_words("click file in the top left corner", said))
        self.assertTrue(talk.own_words("then choose save as", said))
        self.assertFalse(talk.own_words("no I meant the other window", said))
        self.assertFalse(talk.own_words("save as", said))               # too short to be sure
        self.assertEqual(talk.join_turns("What is this.", "on the left side"), "What is this on the left side")


class Turns(unittest.TestCase):
    def taker(self, texts):
        self.heard = []

        def transcribe(audio):
            self.heard.append(len(audio) / RATE)
            return texts.pop(0) if texts else ""

        return talk.TurnTaker(FakeListening, transcribe)

    def feed(self, taker, audio):
        """[(seconds into `audio`, event)]"""
        events, at = [], 0.0
        for chunk in steps(audio):
            at += len(chunk) / RATE
            events += [(at, event) for event in taker.feed(chunk)]
        return events

    def test_a_finished_question_is_answered_at_the_first_pause(self):
        taker = self.taker(["What is on my screen?"])
        events = self.feed(taker, np.concatenate([quiet(0.5), voice(1.0, 1), quiet(2.0)]))
        self.assertEqual([event[0] for _, event in events], ["speech", "turn"])
        at, turn = events[1]
        self.assertEqual(turn[1:], ("What is on my screen?", talk.COMPLETE))
        self.assertLess(at - 1.5, 0.45)                         # 0.3 s of silence, not 0.7
        self.assertFalse(taker.active)
        self.assertEqual(len(taker.decisions), 1)

    def test_an_unfinished_turn_waits_and_then_goes_anyway(self):
        taker = self.taker(["open the file and"])
        events = self.feed(taker, np.concatenate([voice(1.0, 1), quiet(3.0)]))
        at, turn = events[-1]
        self.assertEqual(turn[2], talk.UNFINISHED)
        self.assertGreater(at - 1.0, 1.5)
        self.assertLess(at - 1.0, 1.8)

    def test_a_pause_in_the_middle_does_not_split_the_turn(self):
        taker = self.taker(["open the file and", "open the file and click save"])
        audio = np.concatenate([voice(1.0, 1), quiet(1.2), voice(1.0, 2), quiet(1.0)])
        turns = [event for _, event in self.feed(taker, audio) if event[0] == "turn"]
        self.assertEqual(turns, [("turn", "open the file and click save", talk.COMPLETE)])
        self.assertGreater(self.heard[1], 3.0)                  # the second reading had both halves

    def test_a_noise_is_not_a_turn(self):
        taker = self.taker([""])
        events = self.feed(taker, np.concatenate([voice(0.4, 1), quiet(2.5)]))
        self.assertEqual([event[0] for _, event in events], ["speech"])
        self.assertFalse(taker.active)

    def test_buffers_start_afresh_when_nobody_talks(self):
        taker = self.taker([])
        self.feed(taker, quiet(talk.TurnTaker.IDLE + 1.0))
        self.assertLess(taker.samples, 2 * RATE)


class Echo(unittest.TestCase):
    DELAY = int(0.08 * RATE)

    def run_gate(self, leak, seconds=12.0, user_at=None, blind=False, louder=4):
        """Buddy speaks; its voice leaks into the microphone `leak` times as loud. The user talks over
        it `louder` times as loud as that echo, and never below a normal speaking level (a headset).
        Returns when the gate said "the user" (seconds), or None."""
        gate = talk.EchoGate()
        buddy = voice(seconds, 1)
        echo = np.concatenate([np.zeros(self.DELAY, dtype=np.float32), buddy])[:len(buddy)] * leak
        mic = echo + quiet(seconds)
        if user_at is not None:
            start = int(user_at * RATE)
            mic[start:] += voice(seconds, 2)[:len(mic) - start] * max(leak * louder, 0.5)
        at = 0.0
        for played, heard in zip(steps(buddy), steps(mic)):
            if not blind:
                gate.played(played, RATE, at)
            at += len(heard) / RATE
            if gate.hear(heard, at, True if blind else None):
                return at
        return None

    def test_own_voice_never_counts_as_the_user(self):
        for leak in (0.02, 0.2, 0.8, 2.0):
            self.assertIsNone(self.run_gate(leak), f"leak {leak}")
            self.assertIsNone(self.run_gate(leak, blind=True), f"blind, leak {leak}")

    def test_the_user_is_heard_within_half_a_second(self):
        for leak in (0.02, 0.2, 0.8):
            for blind in (False, True):
                # a voice that does not report its samples needs the user clearly louder
                at = self.run_gate(leak, user_at=5.0, blind=blind, louder=6 if blind else 4)
                self.assertIsNotNone(at, f"leak {leak}, blind {blind}")
                self.assertGreater(at, 5.0)
                self.assertLess(at - 5.0, 0.5, f"leak {leak}, blind {blind}")

    def test_nothing_playing_nothing_to_interrupt(self):
        gate = talk.EchoGate()
        at = 0.0
        for chunk in steps(voice(3.0, 2)):
            at += len(chunk) / RATE
            self.assertFalse(gate.hear(chunk, at))
        self.assertFalse(gate.audible(at))


class FakeMic:
    sample_rate = RATE

    def __init__(self):
        self.blocks = []
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeSpeaker:
    def __init__(self, clock, reports=True):
        self.clock = clock
        self.reports = reports
        self.is_speaking = False
        self.is_paused = False
        self.paused_at = []
        self.resumed = 0
        self.recent = []
        self.listener = None

    def speaking(self):
        return self.is_speaking

    def reference(self):
        return self.reports

    def listen(self, callback):
        self.listener = callback

    def pause(self):
        self.is_paused = True
        self.paused_at.append(self.clock())

    def resume(self):
        self.is_paused = False
        self.resumed += 1


class Conversation:
    """A TalkSession with a fake microphone, voice detector, speech engine, voice and brain."""

    DELAY = int(0.08 * RATE)

    def __init__(self, texts, reports=True, thinks=False):
        self.now = 0.0
        self.texts = list(texts)
        self.mic = FakeMic()
        self.speaker = FakeSpeaker(lambda: self.now, reports)
        self.asked, self.interrupts, self.looks, self.ended = [], 0, 0, []
        self.answered = threading.Event()
        if not thinks:
            self.answered.set()
        self.session = talk.TalkSession(
            self.mic, lambda audio: audio, FakeListening, self.transcribe, self.ask, self.interrupt,
            self.speaker, look=self.look, on_end=self.ended.append, clock=lambda: self.now)
        self.speaker.listen(self.session.gate.played)

    def transcribe(self, audio):
        return self.texts.pop(0) if self.texts else ""

    def ask(self, text):
        self.asked.append(text)
        self.answered.wait(5)

    def interrupt(self):
        self.interrupts += 1
        self.speaker.is_speaking = False
        self.speaker.is_paused = False
        self.answered.set()

    def look(self):
        self.looks += 1

    def settle(self):
        if self.session.asking is not None and self.answered.is_set():
            self.session.asking.join(2)

    def hear(self, audio):
        """The user (or the room) for a while; Buddy is silent."""
        for chunk in steps(audio):
            self.mic.blocks.append(chunk)
            self.now += len(chunk) / RATE
            self.session.tick()
            self.settle()

    def buddy_speaks(self, seconds, leak=0.2, user=None):
        """Buddy speaks for `seconds`, heard by the microphone `leak` times as loud. `user` is what the
        user says over it: (seconds after the start, audio). Buddy stops for good once it is interrupted."""
        buddy = voice(seconds, 1)
        size = int(STEP * RATE)
        over = np.zeros(int(seconds * RATE) + 8 * RATE, dtype=np.float32)
        if user is not None:
            start = int(user[0] * RATE)
            over[start:start + len(user[1])] = user[1]
        self.speaker.is_speaking = True
        position, step = 0, 0
        while position < len(buddy) and self.speaker.is_speaking and step * size < len(over) - size:
            heard = over[step * size:(step + 1) * size] + quiet(STEP, seed=step)
            if not self.speaker.is_paused:
                self.speaker.listener(buddy[position:position + size], RATE, self.now)
                echo = buddy[max(0, position - self.DELAY):max(0, position - self.DELAY) + size] * leak
                heard[:len(echo)] += echo if position >= self.DELAY else 0
                position += size
            self.mic.blocks.append(heard)
            self.now += STEP
            step += 1
            self.session.tick()
            self.settle()
        self.speaker.is_speaking = False


class Session(unittest.TestCase):
    def test_a_question_is_asked_once_and_the_screen_read_early(self):
        talking = Conversation(["What is on my screen?"])
        talking.hear(np.concatenate([quiet(0.5), voice(1.0, 2), quiet(1.0)]))
        self.assertEqual(talking.asked, ["What is on my screen?"])
        self.assertEqual(talking.looks, 1)
        self.assertEqual(talking.mic.blocks, [])                # read and dropped: no hour-long buffer
        self.assertEqual(talking.interrupts, 0)

    def test_noise_and_mm_hm_are_not_questions(self):
        talking = Conversation(["", "Mm-hm."])
        talking.hear(np.concatenate([voice(0.5, 2), quiet(1.5), voice(0.5, 3), quiet(1.5)]))
        self.assertEqual(talking.asked, [])

    def test_adding_to_the_question_while_buddy_thinks_asks_it_again_whole(self):
        talking = Conversation(["What is this?", "on the left side"], thinks=True)
        talking.hear(np.concatenate([voice(1.0, 2), quiet(0.6)]))
        self.assertEqual(talking.asked, ["What is this?"])
        talking.session.asking.join(0.05)
        self.assertTrue(talking.session.asking.is_alive())      # Buddy is still thinking
        talking.hear(np.concatenate([voice(1.0, 3), quiet(1.2)]))
        self.assertEqual(talking.asked, ["What is this?", "What is this? on the left side"])
        self.assertEqual(talking.interrupts, 1)
        self.assertEqual(talking.session.stats["repairs"], 1)

    def start_answer(self, texts, reports=True):
        talking = Conversation(["How do I save this file?"] + texts, reports)
        talking.hear(np.concatenate([voice(1.0, 2), quiet(0.6)]))
        talking.speaker.recent = ["Click File in the top left corner.", "Then choose Save As."]
        return talking

    def test_talking_over_buddy_stops_it_within_half_a_second(self):
        for reports in (True, False):                           # natural voice, Windows SAPI voice
            talking = self.start_answer(["No, I meant the other window."], reports)
            started = talking.now
            level = 0.08 if reports else 0.12                   # 4 and 6 times the echo (0.02)
            talking.buddy_speaks(8.0, user=(3.0, voice(1.5, 5, level=level)))
            self.assertEqual(len(talking.speaker.paused_at), 1, f"reports {reports}")
            self.assertLess(talking.speaker.paused_at[0] - (started + 3.0), 0.5)
            self.assertGreater(talking.speaker.paused_at[0], started + 3.0)
            talking.hear(quiet(1.0))
            self.assertEqual(talking.asked[-1], "No, I meant the other window.")
            self.assertEqual(talking.interrupts, 1)
            self.assertEqual(talking.speaker.resumed, 0)

    def test_yeah_lets_buddy_carry_on(self):
        talking = self.start_answer(["Yeah."])
        talking.buddy_speaks(6.0, user=(3.0, voice(0.5, 5, level=0.08)))
        self.assertEqual(len(talking.speaker.paused_at), 1)
        self.assertEqual(talking.speaker.resumed, 1)
        self.assertEqual(talking.interrupts, 0)
        self.assertEqual(len(talking.asked), 1)

    def test_buddy_never_interrupts_itself(self):
        for leak in (0.02, 0.2, 0.8, 2.0):
            for reports in (True, False):
                talking = self.start_answer([], reports)
                talking.buddy_speaks(10.0, leak=leak)
                talking.hear(quiet(1.0))
                self.assertEqual(talking.speaker.paused_at, [], f"leak {leak}, reports {reports}")
                self.assertEqual(len(talking.asked), 1, f"leak {leak}, reports {reports}")

    def test_its_own_sentence_coming_back_is_not_an_interruption(self):
        talking = self.start_answer(["then choose save as"])
        talking.buddy_speaks(1.0)
        talking.session._pause(talking.now, fresh=False)        # as if the echo had fooled the gate
        before = dict(talking.session.gate.gain)
        talking.hear(np.concatenate([voice(1.0, 5), quiet(2.0)]))
        self.assertEqual(talking.speaker.resumed, 1)
        self.assertEqual(talking.interrupts, 0)
        self.assertGreater(talking.session.gate.gain[False], before[False])   # and it is not fooled twice

    def test_a_bang_only_makes_buddy_hesitate(self):
        talking = self.start_answer([])
        talking.buddy_speaks(1.0)
        talking.session._pause(talking.now, fresh=True)
        talking.speaker.is_speaking = True
        talking.hear(quiet(1.0))
        self.assertEqual(talking.speaker.resumed, 1)
        self.assertEqual(talking.interrupts, 0)

    def test_goodbye_idle_and_stop_end_the_conversation(self):
        talking = Conversation(["Goodbye!"])
        talking.hear(np.concatenate([voice(0.8, 2), quiet(1.0)]))
        self.assertEqual(talking.session.reason, "goodbye")
        talking.session.close()
        self.assertTrue(talking.mic.stopped)
        self.assertEqual(talking.ended, ["goodbye"])
        self.assertIsNone(talking.speaker.listener)

        talking = Conversation([])
        talking.hear(quiet(talking.session.idle_seconds + 1.0))
        self.assertEqual(talking.session.reason, "idle")

        talking = Conversation([])
        talking.session.stop("hotkey")
        talking.session.stop()
        self.assertEqual(talking.session.reason, "hotkey")
        talking.session.run()                                   # returns at once and cleans up
        self.assertEqual(talking.ended, ["hotkey"])


class Wiring(unittest.TestCase):
    """The few lines talk mode needs in Buddy's voice and brain."""

    def test_natural_voice_pauses_resumes_and_reports_what_it_plays(self):
        import keyit_voice

        class FakeStream:
            latency = 0.1

            def __init__(self):
                self.written, self.aborted, self.started = 0, 0, 0

            def write(self, piece):
                self.written += len(piece)

            def abort(self):
                self.aborted += 1

            def start(self):
                self.started += 1

        natural = keyit_voice.NaturalVoice.__new__(keyit_voice.NaturalVoice)
        natural.generation, natural.paused = 1, False
        blocks = []

        def on_block(piece, rate, play_time):
            blocks.append(len(piece))
            if len(blocks) == 6:
                natural.paused = True
                threading.Timer(0.1, natural.resume).start()

        natural.on_block = on_block
        stream, rate = FakeStream(), 24000
        natural._play(stream, 1, np.zeros(rate, dtype=np.float32), rate)
        self.assertEqual((stream.aborted, stream.started), (1, 1))
        self.assertEqual(stream.written, rate + int(0.1 * rate))            # the dropped 0.1 s is said again
        self.assertEqual(blocks[0], rate // 20)

        natural.on_block = lambda piece, rate, play_time: natural.stop() if stream.written >= rate // 2 else None
        natural.texts, natural.audio = keyit_voice.queue.Queue(), keyit_voice.queue.Queue()
        stream = FakeStream()
        natural._play(stream, natural.generation, np.zeros(rate, dtype=np.float32), rate)
        self.assertLessEqual(stream.written, rate // 2 + rate // 20)        # stop() within one block

    def buddy_module(self):
        try:
            import keyit_buddy
        except Exception as error:                                          # no Windows, no display
            self.skipTest(f"keyit_buddy needs Windows: {error}")
        return keyit_buddy

    def test_sapi_voice_is_never_left_paused(self):
        module = self.buddy_module()

        class FakeVoice:
            def __init__(self):
                self.calls = []

            def Pause(self):
                self.calls.append("pause")

            def Resume(self):
                self.calls.append("resume")

            def Speak(self, text, flags):
                self.calls.append(("speak", text))

        speaker = module.Speaker.__new__(module.Speaker)
        speaker.voice, speaker.sapi_paused, speaker.sapi_speaking, speaker.watching = FakeVoice(), False, True, False
        for job in (("pause", True), ("pause", True), ("stop", None), ("pause", False), ("watch", True)):
            speaker._job(*job)
        self.assertEqual(speaker.voice.calls, ["pause", ("speak", ""), "resume"])
        self.assertFalse(speaker.sapi_speaking)
        self.assertTrue(speaker.watching)

    def test_a_talk_question_waits_for_the_interrupted_answer(self):
        module = self.buddy_module()
        buddy = module.Buddy.__new__(module.Buddy)
        buddy.busy, buddy.generation, asked = threading.Lock(), 1, []
        buddy._ask_locked = lambda question, generation, selection="": asked.append(question)
        buddy.busy.acquire()
        buddy._ask("dropped, as before")
        threading.Timer(0.1, buddy.busy.release).start()
        buddy._ask("kept", wait=3.0)
        self.assertEqual(asked, ["kept"])
        self.assertFalse(buddy.busy.locked())
        self.assertTrue(module.DEFAULTS["talk_mode"])


if __name__ == "__main__":
    unittest.main()
