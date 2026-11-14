"""Talk mode: a spoken conversation with Buddy, no key held.

Ctrl+Alt+T opens the microphone and keeps it open. Three things make that feel like talking to someone:

1. Turn taking (TurnTaker). A pause is not the end of a turn. At every pause the words so far are read:
   a finished question is answered at once, "open the file and" waits for the rest.
2. Barge-in (EchoGate). Start talking while Buddy speaks and Buddy holds its breath within a few tenths
   of a second. "mm-hm" lets it carry on; anything else becomes the next question.
3. Turn repair. Add something while Buddy is still thinking and the question is asked again, whole.

Everything runs on this PC with what Buddy already has (the Silero voice detector and the speech
engine): no new download, no cloud, no price per minute. bench/talk_turns.py measures it.

No Windows, audio or model imports here: the microphone, the voice detector, the speech engine and
Buddy's voice are handed in, so keyit/tests/test_talk.py drives all of it with fakes.
"""
import collections
import logging
import re
import threading
import time

import numpy as np

RATE = 16000
EMPTY_AUDIO = np.zeros(0, dtype=np.float32)

# --------------------------------------------------------------------------- reading a turn
COMPLETE, NEUTRAL, UNFINISHED, EMPTY = "complete", "neutral", "unfinished", "empty"

# Seconds of silence, counted from the last sound, after which a turn is over - by how finished it reads.
# The voice detector needs 0.3 s of silence to report a pause at all.
GAPS = {COMPLETE: 0.3, NEUTRAL: 0.7, UNFINISHED: 1.6}
FIXED_GAP = GAPS[NEUTRAL]          # what a plain "wait for silence" end-pointer does for every turn

HAN_RANGE = "\u3400-\u4dbf\u4e00-\u9fff"
HAN = re.compile(f"[{HAN_RANGE}]")
TOKEN = re.compile(f"[{HAN_RANGE}]|[^\\W_{HAN_RANGE}]+(?:'[^\\W_]+)*")
TRAILING_OFF = re.compile(r"(\.\.\.|\u2026|,|\uff0c|\u3001|-|\u2014|\u2013)\s*$")

# Last words that promise more. English first, then Indonesian.
HANGING = set(
    "and or but because cause although unless plus if whether so the a an my your our their some every "
    "i we i'm it's that's there's let's um uh er erm hmm umm uhh "
    "dan atau tapi tetapi karena jadi yang untuk dengan lalu terus kalau di ke dari eh em anu".split())
# "what is this for" is whole, "the shortcut for" is not: a question may end on these.
PREPOSITIONS = set("to of with for from in on at by about into onto like as than".split())
AUXILIARIES = set("is are was were am be been can could would should will shall may might must do does did "
                  "have has had".split())
SETTLED = {("think", "so"), ("hope", "so"), ("guess", "so"), ("say", "so"), ("do", "so")}
QUESTION_STARTS = set(
    "what what's who who's where where's when why how how's which whose is are was were do does did can "
    "could would will should shall may have has am isn't aren't don't doesn't can't "
    "apa apakah bagaimana gimana kenapa mengapa dimana siapa kapan berapa bisakah".split())
COMMAND_STARTS = set(
    "show open close click tell explain find help point read go give make take put start stop save copy "
    "paste type write search scroll select move delete create check look try play pause please summarize "
    "translate describe list turn switch press run "
    "tolong coba buka tutup klik jelaskan tunjukkan cari".split())
CLOSERS = {"please", "thanks"}

HAN_HANGING = ("然後", "然后", "但是", "可是", "因為", "因为", "所以", "還有", "还有", "而且", "或者", "或是",
               "如果", "就是", "那個", "那个", "這個", "这个", "的", "和", "跟", "把", "在", "嗯", "呃")
HAN_ASKING = ("嗎", "吗", "呢")
HAN_QUESTIONS = ("什麼", "什么", "怎麼", "怎么", "為什麼", "为什么", "哪", "誰", "谁", "幾", "多少")
HAN_COMMANDS = ("請", "请", "幫我", "帮我", "打開", "打开", "關閉", "关闭", "告訴我", "告诉我")


def words(text):
    """Lower-case words; Chinese characters one by one."""
    return TOKEN.findall(str(text or "").lower().replace("\u2019", "'"))


def completeness(text):
    """How finished a turn reads: COMPLETE, NEUTRAL, UNFINISHED - or EMPTY when there are no words.

    Full stops are not trusted: a speech engine ends whatever it is given with one. Languages without
    rules here (anything but English, Indonesian and Chinese) come out NEUTRAL, i.e. the plain gap."""
    text = " ".join(str(text or "").split())
    tokens = words(text)
    if not tokens:
        return EMPTY
    if TRAILING_OFF.search(text):
        return UNFINISHED
    asked = text.endswith(("?", "\uff1f"))
    if HAN.search(text):
        bare = "".join(tokens)
        if asked or bare.endswith(HAN_ASKING):
            return COMPLETE
        wondering = any(word in bare for word in HAN_QUESTIONS)
        # "這個按鈕是做什麼的" is a whole question although it ends on 的; "桌面上的" is not.
        if bare.endswith(HAN_HANGING) and not (wondering and bare.endswith("的")):
            return UNFINISHED
        if len(bare) >= 4 and (wondering or bare.startswith(HAN_COMMANDS)):
            return COMPLETE
        return NEUTRAL
    first, last = tokens[0], tokens[-1]
    opens = first in QUESTION_STARTS
    if tuple(tokens[-2:]) in SETTLED:
        return NEUTRAL
    if last in HANGING:
        return UNFINISHED
    if last in PREPOSITIONS:
        return NEUTRAL if opens or asked else UNFINISHED
    if last in AUXILIARIES:
        return UNFINISHED if opens else NEUTRAL
    if asked and len(tokens) >= 2:
        return COMPLETE
    if len(tokens) >= 3 and (opens or first in COMMAND_STARTS):
        return COMPLETE
    if last in CLOSERS or tokens[-2:] == ["thank", "you"]:
        return COMPLETE
    return NEUTRAL


# What the user said while Buddy was talking, or between answers.
GOODBYE, INTERRUPT, BACKCHANNEL, NOISE, SPEECH = "goodbye", "interrupt", "backchannel", "noise", "speech"
NOISES = {"mm", "mhm", "mmhmm", "mm hm", "mm hmm", "hm", "hmm", "uh huh", "uh", "oh", "ah", "huh", "嗯", "喔", "哦"}
BACKCHANNELS = NOISES | {
    "yeah", "yes", "yep", "ok", "okay", "right", "i see", "sure", "got it", "cool", "nice", "wow",
    "thanks", "thank you", "對", "对", "好", "好 的", "是", "ya", "iya", "oke", "baik"}
INTERRUPTS = {"stop", "stop it", "wait", "hold on", "no", "nope", "cancel", "quiet", "pause", "enough",
              "never mind", "nevermind", "等 一 下", "等 等", "停", "不 是", "不 對", "不 对",
              "tunggu", "berhenti", "bukan"}
GOODBYES = {"goodbye", "good bye", "bye", "bye bye", "stop listening", "that's all", "thats all", "that is all",
            "end conversation", "talk mode off", "thanks buddy", "thank you buddy", "bye buddy", "goodbye buddy",
            "再 見", "再 见", "拜 拜", "結 束", "结 束", "sampai jumpa", "dadah", "selesai"}


def spoken_kind(text):
    """GOODBYE, INTERRUPT, BACKCHANNEL, NOISE (a listener's "mm-hm") or SPEECH."""
    said = " ".join(words(text))
    if said in GOODBYES:
        return GOODBYE
    if said in INTERRUPTS:
        return INTERRUPT
    if said in NOISES:
        return NOISE
    parts = said.split()
    if said in BACKCHANNELS or (1 < len(parts) <= 3 and all(part in BACKCHANNELS for part in parts)):
        return BACKCHANNEL
    return SPEECH


def own_words(heard, spoken):
    """True when what the microphone "heard" is Buddy's own sentence coming back from the loudspeaker."""
    heard = words(heard)
    said = set(words(" ".join(spoken)))
    if len(heard) < 3 or not said:
        return False
    return sum(word in said for word in heard) / len(heard) >= 0.75


def join_turns(first, second):
    """A question and what the user added to it, as one question."""
    first = str(first or "").strip().rstrip(".\u3002")
    return f"{first} {second}".strip() if first else second


# --------------------------------------------------------------------------- whose turn is it
class TurnTaker:
    """Microphone audio in, whole turns out.

    `new_listening()` gives a keyit_speech.Listening (hear / since); `transcribe(audio16k)` gives text.
    feed() returns events: ("speech",) when someone starts to talk, ("turn", text, kind) when they are done.
    """

    PAD = 0.25             # s of audio kept around the speech that goes to the speech engine
    LONGEST = 45.0         # s after which a turn ends at the next pause, whatever it reads like
    IDLE = 20.0            # s of nothing after which the buffers start afresh

    def __init__(self, new_listening, transcribe, rate=RATE, gaps=GAPS, clock=time.perf_counter):
        self.new_listening = new_listening
        self.transcribe = transcribe
        self.rate = rate
        self.gaps = gaps
        self.clock = clock
        self.decisions = []                        # seconds each "is the turn over?" took (speech engine + rules)
        self.reset()

    def reset(self, start_with=None):
        self.listening = self.new_listening()
        self.chunks = []
        self.samples = 0
        self.first = None                          # sample where this turn's speech began
        self.last = None                           # sample where its latest stretch of speech ended
        self.text = ""
        self.kind = None
        self.talking = False
        return self.feed(start_with) if start_with is not None and len(start_with) else []

    @property
    def active(self):
        """Someone is talking, or has paused in a turn that is not over yet."""
        return self.talking or self.first is not None

    def feed(self, audio16k):
        audio16k = np.asarray(audio16k, dtype=np.float32)
        events = []
        if len(audio16k):
            self.chunks.append(audio16k)
            self.samples += len(audio16k)
        ended = self.listening.hear(audio16k) if len(audio16k) else []
        going = self.listening.since is not None
        if going and not self.talking:
            events.append(("speech",))
        self.talking = going
        if ended:
            if self.first is None:
                self.first = ended[0][0]
            self.last = ended[-1][1]
            if not going:
                self._read()
        if self.first is not None and not going and self.kind is not None:
            silence = (self.samples - self.last) / self.rate
            too_long = (self.samples - self.first) / self.rate > self.LONGEST
            if silence >= self.gaps[self.kind] or too_long:
                events.append(("turn", self.text, self.kind))
                self.reset()
        elif not self.active and self.samples > self.IDLE * self.rate:
            self.reset()
        return events

    def _read(self):
        """A pause: what has been said so far, and does it sound finished?"""
        pad = int(self.PAD * self.rate)
        audio = np.concatenate(self.chunks)[max(0, self.first - pad):self.last + pad]
        started = self.clock()
        text = str(self.transcribe(audio) or "").strip()
        kind = completeness(text)
        self.decisions.append(self.clock() - started)
        if kind == EMPTY:                          # a cough, a door: not a turn
            self.first = self.last = self.kind = None
            self.text = ""
            return
        self.text, self.kind = text, kind


# --------------------------------------------------------------------------- Buddy's voice or the user's
def _level(samples):
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))) if len(samples) else 0.0


class EchoGate:
    """Tells the user's voice from Buddy's own voice coming back through the microphone.

    There is no echo canceller, so this compares loudness: it learns how loud Buddy's voice arrives at
    the microphone (the "gain") and calls it the user only when the microphone is clearly louder than
    the loudest echo that could be arriving now. On a headset that is any speech; next to loud speakers
    the user has to speak up - or press the hotkey - because nothing here can hear through the echo.

    Natural voices report their samples (played); Windows SAPI voices do not, so there the gate runs
    blind on "Buddy is speaking" alone.
    """

    FRAME = 0.02           # s per loudness measurement
    WINDOW = 0.6           # s of playback the echo of "now" may come from (output delay + the room)
    EARLY = 0.15           # s the playback clock may run ahead of the loudspeaker
    MARGIN = 1.5           # the user has to be this much louder than the loudest echo expected now
    BLIND_MARGIN = 2.5     # ... and this much when the echo can only be guessed (SAPI voices)
    LEARN = 0.5            # s at the start of the first answer when the echo is only measured
    HEADROOM = 1.25        # on top of the measured echo
    RISE = 0.05            # share of a louder echo taken over per frame
    NEED, OUT_OF = 5, 12   # frames that have to be the user's before it counts (0.1 s of 0.24 s)
    HANGOVER = 0.35        # s after the last sample when the room still rings
    QUIET = 1e-4           # playback below this level is silence
    FLOOR = 0.002          # a level no user frame is ever below (about -54 dBFS)

    def __init__(self, rate=RATE):
        self.rate = rate
        self.size = int(rate * self.FRAME)
        self.left = EMPTY_AUDIO
        self.lock = threading.Lock()
        self.playback = collections.deque()        # (time, level) of what the loudspeaker plays
        self.gain = {False: 0.0, True: 0.0}        # echo at the microphone per unit of playback; key: blind
        self.learnt = {False: 0.0, True: 0.0}      # s of echo measured so far
        self.seen = {mode: collections.deque(maxlen=int(2.0 / self.FRAME)) for mode in (False, True)}
        self.noise = None                          # level of the room when Buddy is silent
        self.votes = collections.deque(maxlen=self.OUT_OF)
        self.audible_until = float("-inf")

    def played(self, samples, rate, at):
        """Buddy's voice: `samples` reach the loudspeaker at time `at` (same clock as hear)."""
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        size = max(1, int(rate * self.FRAME))
        with self.lock:
            for start in range(0, len(samples), size):
                self.playback.append((at + start / rate, _level(samples[start:start + size])))
            while self.playback and self.playback[0][0] < at - 3.0:
                self.playback.popleft()
        self.audible_until = max(self.audible_until, at + len(samples) / rate + self.HANGOVER)

    def audible(self, now):
        """Buddy's voice is in the room (or was a moment ago)."""
        return now < self.audible_until

    def clear(self):
        self.votes.clear()

    def louder(self):
        """That was Buddy's own voice after all: the echo is louder than was thought."""
        for mode in self.gain:
            self.gain[mode] *= 1.5

    def hear(self, audio16k, now, speaking=None):
        """Microphone audio that ended at `now`. True when the user has started to talk over Buddy.
        `speaking` is only given for voices that do not report their samples."""
        blind = speaking is not None
        if blind and speaking:
            self.audible_until = max(self.audible_until, now + self.HANGOVER)
        audio = np.concatenate([self.left, np.asarray(audio16k, dtype=np.float32)])
        count = len(audio) // self.size
        self.left = audio[count * self.size:]
        if not count:
            return sum(self.votes) >= self.NEED
        with self.lock:
            playback = list(self.playback)
        times = np.array([at for at, _ in playback])
        levels = np.array([level for _, level in playback])
        for index in range(count):
            at = now - (count - index) * self.FRAME
            if blind:
                reference = 1.0 if at < self.audible_until else 0.0
            else:
                near = levels[(times >= at - self.WINDOW) & (times <= at + self.EARLY)]
                reference = float(near.max()) if len(near) else 0.0
            level = _level(audio[index * self.size:(index + 1) * self.size])
            self.votes.append(self._users(level, reference, blind))
        return sum(self.votes) >= self.NEED

    def _users(self, level, reference, blind):
        """Is this frame the user's voice? Keeps the picture of the echo and the room up to date."""
        if reference < self.QUIET:
            self.noise = level if self.noise is None else min(level, self.noise * 1.02 + 1e-6)
            return False
        noise = self.noise or 0.0
        observed = max(level - noise, 0.0) / reference
        seen = self.seen[blind]
        seen.append(observed)
        if self.learnt[blind] < self.LEARN:
            self.learnt[blind] += self.FRAME
            self.gain[blind] = max(self.gain[blind], observed * self.HEADROOM)
            return False
        gain = self.gain[blind]
        margin = self.BLIND_MARGIN if blind else self.MARGIN
        if level > margin * gain * reference + 3 * noise + self.FLOOR:
            return True
        if observed > gain:                                       # the echo got a little louder
            self.gain[blind] = gain + (observed - gain) * self.RISE
        elif len(seen) == seen.maxlen and max(seen) * self.HEADROOM < 0.5 * gain:
            self.gain[blind] = max(seen) * self.HEADROOM          # quieter for two seconds: volume turned down
        return False


# --------------------------------------------------------------------------- the conversation
class TalkSession:
    """One conversation, from Ctrl+Alt+T to "goodbye". A thread calls tick() about 30 times a second.

    mic            started microphone with `.blocks` (list of float arrays) and stop()
    to16k          audio at the microphone's rate -> 16 kHz (keyit_speech.Stream16k(...).feed)
    new_listening  -> a fresh keyit_speech.Listening
    transcribe     16 kHz audio -> text
    ask            text -> None; blocks while Buddy thinks and queues its answer on the speaker
    interrupt      stop the answer that is being thought or spoken
    speaker        speaking() pause() resume() listen(callback) reference() and `.recent` texts
    """

    TICK = 0.03
    RESUME_AFTER = 0.6     # s Buddy stays quiet for a sound that turns out not to be speech
    HEARD = 0.5            # s of an answer after which a new turn is a new question, not an addition
    PREROLL = 0.6          # s of audio from before a barge-in was noticed that go to the speech engine

    def __init__(self, mic, to16k, new_listening, transcribe, ask, interrupt, speaker,
                 show=lambda state: None, look=lambda: None, on_end=lambda reason: None,
                 idle_seconds=60.0, clock=time.monotonic, sleep=time.sleep):
        self.mic = mic
        self.to16k = to16k
        self.ask = ask
        self.interrupt = interrupt
        self.speaker = speaker
        self.show = show
        self.look = look
        self.on_end = on_end
        self.idle_seconds = idle_seconds
        self.clock = clock
        self.sleep = sleep
        self.turns = TurnTaker(new_listening, transcribe)
        self.gate = EchoGate()
        self.stopped = threading.Event()
        self.reason = None
        self.asking = None                         # thread of the question Buddy is answering
        self.question = ""
        self.paused_at = None                      # when Buddy held its breath for the user
        self.was_audible = False
        self.audible_for = 0.0                     # s of the current answer that have been in the room
        self.before = collections.deque()          # the last PREROLL seconds of microphone audio
        self.ticked_at = None
        self.active_at = clock()
        self.stats = {"turns": 0, "repairs": 0, "pauses": 0, "resumed": 0, "interrupted": 0}

    # -- life cycle
    def start(self):
        self.speaker.listen(self.gate.played)
        threading.Thread(target=self.run, name="buddy-talk", daemon=True).start()

    def stop(self, reason="cancelled"):
        if self.reason is None:
            self.reason = reason
        self.stopped.set()

    def run(self):
        try:
            while not self.stopped.is_set():
                self.tick()
                self.sleep(self.TICK)
        except Exception:
            logging.exception("Talk mode stopped")
            self.stop("error")
        finally:
            self.close()

    def close(self):
        try:
            self.mic.stop()
        except Exception:
            pass
        self.speaker.listen(None)
        if self.paused_at is not None:
            self.speaker.resume()
        decisions = sorted(self.turns.decisions)
        slowest = decisions[int(0.95 * (len(decisions) - 1))] if decisions else 0.0
        logging.info("Talk mode ended (%s): %s, slowest turn decision %.0f ms", self.reason, self.stats,
                     slowest * 1000)
        self.on_end(self.reason)

    # -- one step
    def _new_audio(self):
        blocks = self.mic.blocks
        count = len(blocks)
        if not count:
            return EMPTY_AUDIO
        audio = np.concatenate(blocks[:count])
        del blocks[:count]                         # the callback only appends, so this is safe
        del getattr(self.mic, "anchors", [])[:]
        return np.asarray(self.to16k(audio), dtype=np.float32)

    def tick(self):
        now = self.clock()
        elapsed = 0.0 if self.ticked_at is None else now - self.ticked_at
        self.ticked_at = now
        audio = self._new_audio()
        speaking = self.speaker.speaking()
        answering = speaking or (self.asking is not None and self.asking.is_alive())
        over = self.gate.hear(audio, now, None if self.speaker.reference() else speaking)
        audible = self.gate.audible(now)
        self.before.append(audio)
        while sum(len(chunk) for chunk in self.before) > self.PREROLL * RATE + len(audio):
            self.before.popleft()

        if self.paused_at is not None:
            self._while_paused(audio, now)
        elif audible:
            self.audible_for += elapsed
            self.was_audible = True
            if self.turns.active:                  # the user was already talking when the answer began
                self._pause(now, fresh=False)
                self._after_pause(self.turns.feed(audio))
            elif over:
                self._pause(now, fresh=True)
        elif self.was_audible:                     # Buddy just went quiet: listen with a clean slate
            self.was_audible = False
            self.turns.reset()
        else:
            for event in self.turns.feed(audio):
                if event[0] == "turn":
                    self._turn(event[1], answering)
                else:
                    if not answering:
                        self.look()
                    self.show("listening")

        if answering or self.turns.active or self.paused_at is not None:
            self.active_at = now
        elif now - self.active_at > self.idle_seconds:
            self.stop("idle")

    # -- what to do with what was said
    def _turn(self, text, answering):
        kind = spoken_kind(text)
        if kind == GOODBYE:
            if answering:
                self.interrupt()
            self.stop("goodbye")
            return
        if kind == NOISE or (answering and kind == BACKCHANNEL):
            return                                 # "mm-hm": nothing to answer
        if answering:
            self.interrupt()
            self.stats["interrupted"] += 1
            if kind == INTERRUPT:
                return
            text = self._whole(text)
        self._ask(text)

    def _whole(self, text):
        """Said before Buddy got a word out: it belongs to the question (turn repair)."""
        if self.audible_for < self.HEARD and self.question:
            self.stats["repairs"] += 1
            return join_turns(self.question, text)
        return text

    def _ask(self, text):
        self.stats["turns"] += 1
        self.question = text
        self.audible_for = 0.0
        self.gate.clear()
        self.asking = threading.Thread(target=self.ask, args=(text,), name="buddy-talk-ask", daemon=True)
        self.asking.start()

    def _pause(self, now, fresh):
        self.speaker.pause()
        self.paused_at = now
        self.stats["pauses"] += 1
        self.show("listening")
        if fresh:                                  # hear the start of what the user said, too
            self._after_pause(self.turns.reset(np.concatenate(list(self.before))))

    def _while_paused(self, audio, now):
        self._after_pause(self.turns.feed(audio))
        if self.paused_at is not None and not self.turns.active and now - self.paused_at > self.RESUME_AFTER:
            self._resume()                         # it was not speech after all

    def _after_pause(self, events):
        for event in events:
            if event[0] != "turn":
                continue
            text, kind = event[1], spoken_kind(event[1])
            if kind in (BACKCHANNEL, NOISE) or (
                    kind == SPEECH and own_words(text, getattr(self.speaker, "recent", ()))):
                if kind == SPEECH:
                    self.gate.louder()
                self._resume()
                return
            self.paused_at = None
            self.was_audible = False
            self.interrupt()                       # stops the voice, which ends its pause as well
            self.stats["interrupted"] += 1
            if kind == GOODBYE:
                self.stop("goodbye")
            elif kind == SPEECH:
                self._ask(self._whole(text))
            return

    def _resume(self):
        self.paused_at = None
        self.stats["resumed"] += 1
        self.gate.clear()
        self.turns.reset()
        self.speaker.resume()
