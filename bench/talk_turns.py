"""Talk mode bench: does Buddy know when you have finished, and does it stop when you talk over it?

    python bench/talk_turns.py --cache <folder for synthesized clips>

KEYIT_HOME must point at a folder whose speech/ holds whisper-en and whose voices/ holds Kokoro.
Nothing audible is played: part (d) writes silence to the output device.

(a) turns     hand-labelled utterances, spoken by Kokoro (am_michael), heard by the real voice detector
              and the real speech engine through keyit_talk.TurnTaker. A finished utterance should get its
              turn within BUDGET s of silence; an unfinished one should still be open when the speaker
              goes on after a pause of 0.5 / 0.8 / 1.2 s. Compared with a fixed-silence end-pointer that
              answers after the same mean delay.
(b) decisions how long one "is the turn over?" takes: the rules alone, and speech engine + rules.
(c) barge-in  Buddy's voice (af_heart) through a SIMULATED loudspeaker-to-microphone path (delay, reverb,
              room noise, clipping) into keyit_talk.EchoGate, with and without a user talking over it.
(d) voice     how fast NaturalVoice stops, pauses and resumes on the real output device (silent samples).

The numbers are only as good as the corpus: it is small, hand-written by the author of the rules, and the
speakers are synthetic. HELD_OUT was written after the rules were frozen and was not used to tune them.
"""
import argparse
import hashlib
import json
import os
import platform
import statistics
import sys
import threading
import time
from pathlib import Path

import numpy as np

BENCH = Path(__file__).resolve().parent
RATE = 16000
TICK = 0.03                 # s, TalkSession.TICK
BUDGET = 1.0                # s of silence within which a finished turn has to be taken
ONSET = 0.15                # s the voice detector may need to notice that the speaker went on
PAUSES = (0.5, 0.8, 1.2)    # s, pauses in the middle of an unfinished utterance
USER_VOICE, BUDDY_VOICE = "am_michael", "af_heart"

# --- the corpus -------------------------------------------------------------------------------------------
DEV_FINISHED = [
    "What does this error mean?", "Open the settings.", "Yes, please.", "No, leave it as it is.",
    "How many rows are selected?", "Save the file as a PDF.", "I like the first one better.",
    "Can you read that again?", "Delete the last paragraph.", "That is all for now.",
    "Where did the toolbar go?", "The printer is not responding.", "Make it a bit darker.",
    "Is this the latest version?", "Scroll down to the end.", "It worked this time.",
]
DEV_UNFINISHED = [
    "I want to rename the", "Can you copy this to", "Select the first row and then", "The issue is that",
    "Maybe we could", "Put it next to the", "It looks fine but", "Change the width and",
    "What I am trying to say is", "Tell me if", "Save it in", "It failed because",
    "Um, so the, uh", "Schedule it for", "Use the blue one, or", "We have to do this before",
]
HELD_OUT_FINISHED = [
    "What time is it in Tokyo right now?", "Open the downloads folder.", "How do I merge these two cells?",
    "Summarize this email for me.", "Yes, go ahead.", "No, that's not what I meant.",
    "Can you make the font bigger?", "I think the second option is better.",
    "Translate this paragraph into Chinese.", "Why is my laptop so slow today?",
    "Remind me to call the supplier at three.", "That looks good, thanks.", "Who should I send this to?",
    "What is this button for?", "Show me where the settings are.",
    "The meeting got moved to Thursday afternoon.", "Close all the tabs except this one.",
    "I don't know what that error means.", "Is there a faster way to do it?",
    "Read the last message out loud.", "Tell me what you see on the screen.",
    "It crashed again when I pressed save.", "Which file did I edit most recently?", "Let's try the other one.",
]
HELD_OUT_UNFINISHED = [
    "I want to open the", "Can you send this to", "First select the column and then", "The problem is that",
    "I was thinking we could", "Move this file into the folder called", "It should be bigger but",
    "Change the color of the title and", "So basically what I need is",
    "Could you look at the chart and tell me if", "Put the total at the bottom of",
    "I tried restarting it because", "Compare this version with", "Um, how do I, uh",
    "Set a reminder for tomorrow at", "Open the report, or", "We need to finish this before",
    "The file is saved in my", "Let me think, it was something like",
    "Add a row between the second and the third and also", "I would rather", "Can you check whether the",
    "It needs to go from here to, hmm", "After that we should probably",
]
# Sounds finished, but the speaker adds to it after the pause. No text rule can know; reported on its own.
HELD_OUT_ADDED_TO = [
    "Open the spreadsheet", "I need the report", "Send it to the team", "Make the title bold",
    "There is an error in line twelve", "I want to change the background", "Search for the invoice",
]
# Typed text only (no audio): the rules for the two other languages they cover.
OTHER_LANGUAGES = {
    "zh": (["這個按鈕是做什麼的", "請幫我打開設定", "現在幾點了", "好的,謝謝", "把這一段翻譯成英文", "為什麼電腦這麼慢",
            "我覺得第二個比較好", "可以再說一次嗎"],
           ["我想要打開那個", "先選這一欄然後", "問題是因為", "這個可以但是", "把檔案放到", "我覺得還有",
            "你可以幫我把這個", "嗯,就是那個"]),
    "id": (["Jam berapa sekarang?", "Tolong buka pengaturan.", "Apa arti pesan ini?", "Ya, lanjutkan saja.",
            "Simpan file ini sebagai PDF.", "Kenapa laptop saya lambat sekali?", "Saya lebih suka yang pertama.",
            "Bisa ulangi lagi?"],
           ["Saya mau buka", "Tolong kirim ini ke", "Pilih kolom pertama lalu", "Masalahnya adalah",
            "Ini sudah bagus tapi", "Simpan di dalam", "Gagal karena", "Ubah warnanya dan"]),
}
BUDDY_SAYS = [
    "Sure. To merge the cells, select both of them first, then open the layout tab on the ribbon.",
    "After that, choose merge and center. If the button is grey, the sheet is probably protected.",
    "In that case, go to the review tab and unprotect the sheet before you try again.",
    "Would you like me to walk you through it step by step while you click?",
]
USER_INTERRUPTS = "Wait, stop. I meant the other file, the one from last week."

JEV = {
    "label": "published by the vendor, not measured by us",
    "decision_ms": "about 300 ms per decision", "end_to_end_ms": "70 to 500",
    "price": "$0.042 per million input tokens", "runs": "vendor cloud only", "input": "a transcript",
    "sources": ["https://evalgent.com/blog/jev-voice-agent-use-cases", "https://evalgent.com/blog/jev-for-voice-agents",
                "https://evalgent.com/blog/jev-vs-llm-voice-agents", "https://veris.ai/blog/jev-turn-detection",
                "https://devwithjev.com/builds/full-duplex-voice-agent"],
}


# --- helpers ----------------------------------------------------------------------------------------------
def spread(values, unit=1.0, digits=3):
    values = sorted(values)
    if not values:
        return None
    pick = lambda share: values[min(len(values) - 1, int(round(share * (len(values) - 1))))]
    return {"n": len(values), "p50": round(pick(0.5) * unit, digits), "p95": round(pick(0.95) * unit, digits),
            "max": round(values[-1] * unit, digits), "mean": round(statistics.fmean(values) * unit, digits)}


class Load:
    """System-wide CPU load while a part runs (psutil if it is there)."""

    def __init__(self):
        try:
            import psutil
            self.psutil = psutil
        except ImportError:
            self.psutil = None
        self.samples = []
        if self.psutil:
            threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self):
        while True:
            self.samples.append(self.psutil.cpu_percent(interval=1.0))

    def since(self, start):
        if not self.psutil:
            return "not measured (psutil is not installed)"
        taken = self.samples[start:] or [self.psutil.cpu_percent(interval=1.0)]
        return {"mean_percent": round(statistics.fmean(taken), 1), "max_percent": round(max(taken), 1),
                "logical_cpus": os.cpu_count()}

    def mark(self):
        return len(self.samples)


def trim(samples, rate, floor=0.02):
    """Cut the silence Kokoro puts around a clip, so that pauses are as long as they are meant to be."""
    level = np.abs(samples)
    loud = np.flatnonzero(level > floor * level.max())
    pad = int(0.01 * rate)
    return samples[max(0, loud[0] - pad):loud[-1] + pad]


def active_level(samples, rate):
    frame = int(0.02 * rate)
    levels = np.array([np.sqrt(np.mean(np.square(samples[start:start + frame], dtype=np.float64)))
                       for start in range(0, len(samples) - frame, frame)])
    return float(np.sqrt(np.mean(np.square(levels[levels > 0.1 * levels.max()]))))


class Clips:
    def __init__(self, cache, voice, resample):
        self.cache, self.voice, self.resample = Path(cache), voice, resample
        self.cache.mkdir(parents=True, exist_ok=True)
        self.seconds = []

    def get(self, text, speaker, rate=RATE, trimmed=True):
        name = hashlib.sha1(f"{speaker}|{text}".encode("utf-8")).hexdigest()[:16]
        path = self.cache / f"{name}.npy"
        if path.exists():
            raw = np.load(path)
        else:
            self.voice.set_voice(speaker)
            started = time.perf_counter()
            raw, made_at = self.voice._speak(text)
            self.seconds.append(time.perf_counter() - started)
            assert made_at == 24000, made_at
            raw = np.asarray(raw, dtype=np.float32)
            np.save(path, raw)
        if trimmed:
            raw = trim(raw, 24000)
        return raw if rate == 24000 else np.asarray(self.resample(raw, 24000, rate), dtype=np.float32)


# --- (a) turns ----------------------------------------------------------------------------------------------
def fire_time(talk, detector, transcribe, speech, rng, gaps=None):
    """Seconds of silence after `speech` until TurnTaker calls the turn over (None: not within 2.5 s)."""
    lead = int(0.5 * RATE)
    audio = np.concatenate([np.zeros(lead, np.float32), speech, np.zeros(int(2.5 * RATE), np.float32)])
    audio = audio + rng.normal(0, 0.0005, len(audio)).astype(np.float32)
    taker = talk.TurnTaker(detector.listen, transcribe, gaps=gaps or talk.GAPS)
    step, end = int(TICK * RATE), lead + len(speech)
    for start in range(0, len(audio), step):
        for event in taker.feed(audio[start:start + step]):
            if event[0] == "turn":
                return (min(start + step, len(audio)) - end) / RATE, event[1], event[2], list(taker.decisions)
    return None, taker.text, taker.kind, list(taker.decisions)


def turns(talk, detector, stt, clips, log, urgent):
    rng = np.random.default_rng(7)
    def heard(audio):                                            # as keyit_speech.Speech.transcribe does it:
        with urgent():                                           # above-normal priority while the user waits
            out = stt.decode(audio)
        return out[0] if isinstance(out, tuple) else out

    rows, decisions = [], []
    plain = {kind: 0.0 for kind in talk.GAPS}                    # fires as soon as the detector hears the end
    for label, texts in (("finished", HELD_OUT_FINISHED), ("unfinished", HELD_OUT_UNFINISHED),
                         ("added_to", HELD_OUT_ADDED_TO)):
        for text in texts:
            speech = clips.get(text, USER_VOICE)
            fired, read, kind, took = fire_time(talk, detector, heard, speech, rng)
            decisions += took
            earliest, *_ = fire_time(talk, detector, lambda audio: "okay then", speech, rng, gaps=plain)
            rows.append({"label": label, "said": text, "heard": read, "kind": kind, "fired": fired,
                         "detector": earliest, "decide_s": [round(value, 3) for value in took]})
            log(f"  {label:10} {kind or '-':10} fired {fired}  heard {read!r}")
    return rows, decisions


def score(rows):
    """Balanced accuracy of Buddy and of fixed-silence end-pointers, on the same clips and detector."""
    finished = [row for row in rows if row["label"] == "finished"]
    unfinished = [row for row in rows if row["label"] == "unfinished"]
    late = lambda fired, pause: fired is None or fired >= pause + ONSET

    def measured(fire):
        taken = [fire(row) for row in finished]
        in_time = [value is not None and value <= BUDGET for value in taken]
        held = {pause: [late(fire(row), pause) for row in unfinished] for pause in PAUSES}
        held_all = [ok for values in held.values() for ok in values]
        answered = [value for value in taken if value is not None]
        return {"finished_in_time": round(sum(in_time) / len(in_time), 3),
                "unfinished_held": round(sum(held_all) / len(held_all), 3),
                "held_by_pause": {str(pause): round(sum(values) / len(values), 3) for pause, values in held.items()},
                "balanced_accuracy": round((sum(in_time) / len(in_time) + sum(held_all) / len(held_all)) / 2, 3),
                "mean_delay_finished_s": round(statistics.fmean(answered), 3) if answered else None}

    def fixed(gap):                                              # what TurnTaker does with one gap for everything
        return lambda row: None if row["detector"] is None else max(
            row["detector"], np.ceil((row["detector_end"] + gap) / TICK - 1e-9) * TICK)

    for row in rows:                                             # the detector reports an end about 0.3 s late:
        row["detector_end"] = (row["detector"] or 0.0) - 0.3     # its min silence. Silence is counted from there.
    buddy = measured(lambda row: row["fired"])

    def live(row):                                               # the decision is made inside the loop, so a turn
        if row["fired"] is None:                                 # cannot end before the speech engine has answered
            return None
        decided = (row["detector"] or 0.0) + (row["decide_s"][-1] if row["decide_s"] else 0.0)
        return max(row["fired"], decided)

    grid = {round(gap, 2): measured(fixed(gap)) for gap in np.arange(0.3, 1.65, 0.05)}
    same = min(grid, key=lambda gap: abs(grid[gap]["mean_delay_finished_s"] - buddy["mean_delay_finished_s"]))
    best = max(grid, key=lambda gap: grid[gap]["balanced_accuracy"])
    added = [row for row in rows if row["label"] == "added_to"]
    return {"buddy": buddy,
            "buddy_with_compute": measured(live),
            "fixed_silence_same_delay": {"gap_s": same, **grid[same]},
            "fixed_silence_0.7": grid[0.7],
            "fixed_silence_best": {"gap_s": best, **grid[best]},
            "added_to_cut_by_pause": {str(pause): round(sum(not late(row["fired"], pause) for row in added)
                                                        / max(1, len(added)), 3) for pause in PAUSES},
            "counts": {"finished": len(finished), "unfinished": len(unfinished), "added_to": len(added)}}


def text_only(talk):
    def accuracy(finished, unfinished):
        done = [talk.completeness(text) != talk.UNFINISHED for text in finished]
        open_ = [talk.completeness(text) == talk.UNFINISHED for text in unfinished]
        return {"finished": round(sum(done) / len(done), 3), "unfinished": round(sum(open_) / len(open_), 3),
                "balanced_accuracy": round((sum(done) / len(done) + sum(open_) / len(open_)) / 2, 3),
                "n": len(done) + len(open_),
                "wrong": [text for text, ok in zip(finished + unfinished, done + open_) if not ok]}

    out = {"en_dev": accuracy(DEV_FINISHED, DEV_UNFINISHED),
           "en_held_out": accuracy(HELD_OUT_FINISHED, HELD_OUT_UNFINISHED)}
    for language, (finished, unfinished) in OTHER_LANGUAGES.items():
        out[language] = accuracy(finished, unfinished)
    return out


def rules_speed(talk):
    texts = DEV_FINISHED + DEV_UNFINISHED + HELD_OUT_FINISHED + HELD_OUT_UNFINISHED
    took = []
    for _ in range(50):
        for text in texts:
            started = time.perf_counter()
            talk.completeness(text)
            took.append(time.perf_counter() - started)
    return spread(took, unit=1000.0, digits=4)


# --- (c) barge-in -------------------------------------------------------------------------------------------
def room(rng, reverb, rate=24000):
    """Impulse response of the loudspeaker-to-microphone path: the direct sound and a decaying tail."""
    if not reverb:
        return np.array([1.0])
    tail = rng.normal(0, 1, int(reverb * rate)) * np.exp(-6.9 * np.arange(int(reverb * rate)) / (reverb * rate))
    tail[0] = 0.0
    tail *= np.sqrt(0.5) / np.sqrt(np.sum(tail ** 2))            # tail carries half the energy of the direct sound
    tail[0] = 1.0
    return tail


def convolve(signal, kernel):
    """np.convolve by FFT: the same numbers, and seconds instead of minutes for a 0.6 s room."""
    if len(kernel) == 1:
        return signal * kernel[0]
    size = len(signal) + len(kernel) - 1
    width = 1 << (size - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(signal, width) * np.fft.rfft(kernel, width), width)[:size]


def play(talk, buddy24, echo16, user16, onset, blind, level_noise, rng, until=None, latency=0.1):
    """One run of the session's loop around EchoGate. Returns the times at which it said "the user"."""
    gate = talk.EchoGate()
    start = 1.0                                                  # s of room before Buddy speaks
    length = start + len(buddy24) / 24000 + 0.5
    mic = rng.normal(0, level_noise, int(length * RATE)).astype(np.float32)
    first = int((start + latency) * RATE)
    mic[first:first + len(echo16)] += echo16[:len(mic) - first]
    if user16 is not None:
        at = int(onset * RATE)
        mic[at:at + len(user16)] += user16[:len(mic) - at]
    mic = np.clip(mic, -1.0, 1.0)
    block, reported, said, took = 1200, 0, [], []
    step = int(TICK * RATE)
    for index in range(0, int((until or length) * RATE) - step, step):
        now = (index + step) / RATE
        while not blind and reported < len(buddy24) and start + reported / 24000 <= now:
            gate.played(buddy24[reported:reported + block], 24000, start + reported / 24000 + latency)
            reported += block
        speaking = (start <= now <= start + len(buddy24) / 24000 + latency) if blind else None
        started = time.perf_counter()
        users = gate.hear(mic[index:index + step], now, speaking)
        took.append(time.perf_counter() - started)
        if users:
            said.append(now)
            gate.clear()
    return said, took


def barge_in(talk, clips, log):
    rng = np.random.default_rng(11)
    resample = clips.resample
    buddy24 = np.concatenate([np.concatenate([clips.get(text, BUDDY_VOICE, 24000, trimmed=False),
                                              np.zeros(6000, np.float32)]) for text in BUDDY_SAYS])
    user = clips.get(USER_INTERRUPTS, USER_VOICE)
    user = user / active_level(user, RATE)
    loud = active_level(buddy24, 24000)
    result = {"buddy_seconds": round(len(buddy24) / 24000, 1), "own_voice": [], "user": [], "hear_ms": None}
    took_all = []

    def echo(leak, delay, reverb):
        wet = convolve(buddy24, room(rng, reverb))[:len(buddy24) + 12000]
        wet = np.concatenate([np.zeros(int(delay * 24000)), wet])
        wet = wet * (leak * loud / active_level(wet, 24000))     # leak = echo level at the mic / playback level
        return np.asarray(resample(wet.astype(np.float32), 24000, RATE), dtype=np.float32)

    for blind in (False, True):
        for leak in (0.02, 0.2, 0.8, 2.0):
            events, runs = 0, 0
            for delay in (0.03, 0.08, 0.2):
                for reverb in (0.0, 0.3, 0.6):
                    said, took = play(talk, buddy24, echo(leak, delay, reverb), None, None, blind, 0.0005, rng)
                    events, runs = events + len(said), runs + 1
                    took_all += took
            result["own_voice"].append({"voice": "blind (SAPI)" if blind else "natural", "leak": leak, "runs": runs,
                                        "buddy_minutes": round(runs * len(buddy24) / 24000 / 60, 1),
                                        "false_barge_ins": events})
            log(f"  own voice  {'blind' if blind else 'natural':8} leak {leak:<5} false barge-ins {events} in {runs} runs")
    onsets = [2.6, 3.7, 4.9, 6.2, 7.4, 8.8, 10.1, 11.5]
    for blind in (False, True):
        for name, ratio in (("headset", None), ("x1.5", 1.5), ("x2", 2.0), ("x3", 3.0), ("x4", 4.0), ("x6", 6.0),
                            ("x10", 10.0)):
            waits, missed, early = [], 0, 0
            for leak in ((0.02,) if ratio is None else (0.05, 0.2, 0.5)):
                echoed = echo(leak, 0.08, 0.3)
                level = 0.05 if ratio is None else ratio * leak * loud
                for onset in onsets:
                    said, took = play(talk, buddy24, echoed, user * level, onset, blind, 0.0005, rng, until=onset + 1.5)
                    early += sum(1 for at in said if at < onset)
                    after = [at - onset for at in said if at >= onset]
                    if after and after[0] <= 1.0:
                        waits.append(after[0])
                    else:
                        missed += 1
            tries = len(waits) + missed
            result["user"].append({"voice": "blind (SAPI)" if blind else "natural", "user_vs_echo": name,
                                   "tries": tries, "noticed_within_0.5s": round(sum(w <= 0.5 for w in waits) / tries, 3),
                                   "noticed_within_1s": round(len(waits) / tries, 3), "before_onset": early,
                                   "onset_to_stop_s": spread(waits)})
            log(f"  user {name:8} {'blind' if blind else 'natural':8} noticed {len(waits)}/{tries} {spread(waits)}")
    result["hear_ms"] = spread(took_all, unit=1000.0, digits=4)
    return result


# --- (d) the voice on the real output device -------------------------------------------------------------------
def voice_reaction(voice, log):
    stamps = []
    voice.on_block = lambda samples, rate, at: stamps.append(time.monotonic())
    stops, pauses, resumes = [], [], []
    try:
        for _ in range(8):
            del stamps[:]
            voice.audio.put((voice.generation, np.zeros(24000 * 4, np.float32), 24000))
            deadline = time.monotonic() + 20
            while len(stamps) < 12 and time.monotonic() < deadline:
                time.sleep(0.005)
            if len(stamps) < 12:
                return {"measured": False, "why": "the output device did not start"}
            asked = time.monotonic()
            voice.pause()
            time.sleep(0.4)
            after = [stamp for stamp in stamps if stamp > asked]
            pauses.append((after[-1] - asked if after else 0.0) + 0.05)    # the block being written still plays
            count = len(stamps)
            asked = time.monotonic()
            voice.resume()
            while len(stamps) == count and time.monotonic() < asked + 5:
                time.sleep(0.002)
            resumes.append(time.monotonic() - asked)
            time.sleep(0.3)
            asked = time.monotonic()
            voice.stop()
            while voice.playing and time.monotonic() < asked + 5:
                time.sleep(0.001)
            stops.append(time.monotonic() - asked)
            time.sleep(0.2)
    except Exception as error:
        return {"measured": False, "why": f"{type(error).__name__}: {error}"}
    finally:
        voice.on_block = None
    log(f"  voice  pause {spread(pauses)}  stop {spread(stops)}")
    return {"measured": True, "samples": "silence", "pause_to_silent_s": spread(pauses),
            "resume_to_sound_s": spread(resumes), "stop_to_idle_s": spread(stops)}


# --- report -------------------------------------------------------------------------------------------------
def report(result):
    turns_, speed, barge = result.get("turns"), result.get("decisions"), result.get("barge_in")
    lines = ["# Talk mode bench", "", f"Run {result['when']} on {result['machine']}.", ""]
    if turns_:
        lines += ["## (a) Turns: finished or not (English, real voice detector + whisper-en on Kokoro speech)", "",
                  f"{turns_['counts']['finished']} finished and {turns_['counts']['unfinished']} unfinished held-out "
                  f"utterances; pauses {PAUSES} s; a finished turn has to be taken within {BUDGET} s of silence. "
                  "Delays are on the audio timeline; compute time is in (b).", "",
                  "| end-pointer | finished taken in time | unfinished not cut | balanced accuracy | mean delay, finished |",
                  "|---|---|---|---|---|"]
        for name, key in (("Buddy (reads the words)", "buddy"),
                          ("Buddy, with compute under this load", "buddy_with_compute"),
                          ("fixed silence, same delay", "fixed_silence_same_delay"),
                          ("fixed silence 0.7 s", "fixed_silence_0.7"), ("fixed silence, its best gap", "fixed_silence_best")):
            if key not in turns_:
                continue
            row = turns_[key]
            gap = f" ({row['gap_s']} s)" if "gap_s" in row else ""
            lines.append(f"| {name}{gap} | {row['finished_in_time']:.0%} | {row['unfinished_held']:.0%} | "
                         f"{row['balanced_accuracy']:.1%} | {row['mean_delay_finished_s']} s |")
        shares = lambda by_pause: ", ".join(f"{pause} s pause {share:.0%}" for pause, share in by_pause.items())
        lines += ["", f"Buddy, unfinished not cut, by pause length: {shares(turns_['buddy']['held_by_pause'])}.", "",
                  f"Sounds finished but the speaker adds to it ({turns_['counts']['added_to']} clips, not in the "
                  f"accuracy): answered before the speaker went on, {shares(turns_['added_to_cut_by_pause'])}.", ""]
        lines += ["Rules on typed text (no audio): " + ", ".join(
            f"{name} {row['balanced_accuracy']:.0%} (n={row['n']})" for name, row in result["text_only"].items()) + ".", ""]
    if speed:
        lines += ["## (b) One decision", "", f"CPU load during the run: {speed['load']}.", "",
                  "| what | p50 | p95 | n |", "|---|---|---|---|",
                  f"| rules alone, given the words (what Jev is given) | {speed['rules_ms']['p50']} ms | "
                  f"{speed['rules_ms']['p95']} ms | {speed['rules_ms']['n']} |"]
        if speed.get("stt_and_rules_s"):
            lines.append(f"| speech engine (whisper-en) + rules | {speed['stt_and_rules_s']['p50']} s | "
                         f"{speed['stt_and_rules_s']['p95']} s | {speed['stt_and_rules_s']['n']} |")
        lines += ["", f"Jev ({JEV['label']}): {JEV['decision_ms']}, {JEV['runs']}, {JEV['price']}.", ""]
    if barge:
        lines += ["## (c) Barge-in (SIMULATED loudspeaker-to-microphone path)", "",
                  "| voice | echo at the mic / playback | Buddy speech | false barge-ins |", "|---|---|---|---|"]
        lines += [f"| {row['voice']} | {row['leak']} | {row['buddy_minutes']} min | {row['false_barge_ins']} |"
                  for row in barge["own_voice"]]
        lines += ["", "| voice | user vs echo at the mic | noticed within 0.5 s | within 1 s | onset to stop, p50 | p95 |",
                  "|---|---|---|---|---|---|"]
        for row in barge["user"]:
            wait = row["onset_to_stop_s"] or {"p50": "-", "p95": "-"}
            lines.append(f"| {row['voice']} | {row['user_vs_echo']} | {row['noticed_within_0.5s']:.0%} | "
                         f"{row['noticed_within_1s']:.0%} | {wait['p50']} | {wait['p95']} |")
        lines += ["", f"EchoGate.hear compute per 30 ms tick: p95 {barge['hear_ms']['p95']} ms.", ""]
    if result.get("voice"):
        voice = result["voice"]
        lines += ["## (d) Voice on the real output device (silent samples)", "",
                  f"Not measured: {voice.get('why')}." if voice.get("measured") is False else f"{voice}", ""]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--code", default=str(BENCH.parent / "keyit"))
    parser.add_argument("--out", default=str(BENCH / "results_talk"))
    parser.add_argument("--cache", default=str(BENCH / "results_talk" / "clips"))
    parser.add_argument("--only", default="abcd", help="parts to run, e.g. 'c'")
    args = parser.parse_args()
    sys.path.insert(0, args.code)
    import keyit_speech
    import keyit_talk as talk
    import keyit_voice

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log = lambda text: print(text, flush=True)
    load = Load()
    result_path = out / "talk_turns.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    result.update({"when": time.strftime("%Y-%m-%d %H:%M"),
                   "machine": f"{platform.processor() or platform.machine()}, {os.cpu_count()} logical CPUs, "
                              f"{platform.system()} {platform.release()}",
                   "jev": JEV, "text_only": text_only(talk),
                   "gate": {name: getattr(talk.EchoGate, name) for name in ("MARGIN", "BLIND_MARGIN", "NEED", "OUT_OF")},
                   "gaps_s": dict(talk.GAPS)})
    voice = keyit_voice.NaturalVoice()
    voice.ready.wait(600)
    if voice.failed:
        raise SystemExit(f"Kokoro did not load: {voice.failed}")
    clips = Clips(args.cache, voice, keyit_speech.resample)
    if "a" in args.only or "b" in args.only:
        log("(a) turns")
        for text in HELD_OUT_FINISHED + HELD_OUT_UNFINISHED + HELD_OUT_ADDED_TO:      # synthesize before timing
            clips.get(text, USER_VOICE)
        stt = keyit_speech.SherpaSpeech("whisper-en")
        stt.warm_up()
        mark = load.mark()
        rows, decisions = turns(talk, keyit_speech.VoiceDetector(), stt, clips, log, keyit_speech.urgent)
        result["turn_rows"] = rows
        result["turns"] = score(rows)
        result["decisions"] = {"rules_ms": rules_speed(talk), "stt_and_rules_s": spread(decisions),
                               "load": load.since(mark)}
        log(json.dumps(result["turns"], indent=1))
    if "c" in args.only:
        log("(c) barge-in")
        result["barge_in"] = barge_in(talk, clips, log)
    if "d" in args.only:
        log("(d) voice")
        result["voice"] = voice_reaction(voice, log)
    result["synthesis_s_per_clip"] = spread(clips.seconds)
    result_path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    (out / "talk_turns.md").write_text(report(result), encoding="utf-8", newline="\n")
    log(report(result))


if __name__ == "__main__":
    main()
