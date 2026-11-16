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
import contextlib
import hashlib
import json
import logging
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
# A second held-out list, same recipe as the first. The reads of the first list were looked at while the
# rules were revised, so it is no longer blind. This one was written after the rules were frozen and before
# any clip of it was synthesized or read; it is reported on its own.
HELD_OUT_2_FINISHED = [
    "What is the shortcut for undo?", "Open the calendar.", "How do I freeze the top row?",
    "Reply to this message for me.", "Sure, do that.", "No, I meant the other window.",
    "Could you zoom in a little?", "I prefer the darker theme.", "Convert this table to a chart.",
    "Why did the download stop?", "Send the invoice to accounting tomorrow morning.",
    "Okay, that works for me.", "Where can I find the export option?", "What does this icon do?",
    "Show me the latest sales numbers.", "The screen went black for a second.",
    "Rename this sheet to summary.", "I am not sure which one to pick.",
    "Does this work without the internet?", "Read the first paragraph again.",
    "Explain what this formula does.", "It froze right after the update.", "Which of these is the newest?",
    "Let's leave it for now.",
]
HELD_OUT_2_UNFINISHED = [
    "I need to change the", "Could you move this over to", "Copy the second table and then",
    "The reason I ask is that", "I guess we should", "Save a copy in the folder named",
    "That one is fine but", "Make the header bigger and", "What I really want is",
    "Check the formula and let me know if", "Paste it right below the", "It stopped working because",
    "Replace the old logo with", "Uh, where is the, um", "Book the room for Friday at",
    "Send it today, or", "I have to submit this before", "The password is in my",
    "Hold on, it was called something like", "Sort the list by date and also", "I would prefer to",
    "Do you know whether the", "It goes from the first page to, uh", "Then we can maybe",
]
# Third list, round 3: same author, parallel in form to the second list, written without looking at the rules
# and never read before the run that reports it.
HELD_OUT_3_FINISHED = [
    "How do I merge these two cells?", "Close all the other tabs.", "What time is my next meeting?",
    "Make the font a bit smaller.", "Yes, go ahead.", "No, not that one.", "Can you read that back to me?",
    "I think the second option is better.", "Turn this list into a table.", "Why is the fan so loud?",
    "Email the summary to my manager this afternoon.", "Alright, that looks good.",
    "Where did my screenshot go?", "What does this error mean?", "Show me last week's notes.",
    "The cursor keeps jumping around.", "Move this file to the archive.", "I don't know what that setting does.",
    "Is there a faster way to do this?", "Scroll down to the bottom.", "Tell me what this chart shows.",
    "It crashed when I pressed save.", "Which column has the totals?", "We can stop here.",
]
HELD_OUT_3_UNFINISHED = [
    "I was trying to open the", "Can you send that over to", "Delete the first row and then",
    "The thing I noticed is that", "Maybe we could", "Put the file in the folder called",
    "This one looks right but", "Change the color to blue and", "What I am looking for is",
    "Run the report and tell me whether", "Insert a new line just above the", "It keeps failing because",
    "Swap the first image with", "So, how do I, um", "Set the reminder for Monday at", "Either print it now, or",
    "I need to finish this before", "The link is somewhere in my", "Wait, the file was named something like",
    "Filter the rows by name and also", "I was going to", "Can you tell me if the",
    "It starts on the second slide and, uh", "After that we should probably",
]
LISTS = {"first": "first list (its reads were seen while the rules were revised)",
         "second": "second list (written after the rules were frozen; first read in the round 2 run)",
         "third": "third list (same author, parallel in form to the second; never read before this run)"}
# Sounds finished, but the speaker adds to it after the pause. No text rule can know; reported on its own.
HELD_OUT_ADDED_TO = [
    "Open the spreadsheet", "I need the report", "Send it to the team", "Make the title bold",
    "There is an error in line twelve", "I want to change the background", "Search for the invoice",
]
# What the speaker adds after the pause, one for each line above (turn repair).
CONTINUATIONS = [
    "that I had open yesterday", "with the numbers for March", "and copy my manager", "and a little larger",
    "of the second script", "to something lighter", "from the new supplier",
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

def fire_time(talk, detector, transcribe, speech, rng, gaps=None, last=False):
    """Seconds of silence after `speech` until TurnTaker calls the turn over (None: not within 2.5 s).

    `last`: go on to the end and give the last turn. The plain end-pointer needs that: a detector that reports
    a pause after 0.1 s also reports the short pauses inside a sentence."""
    lead = int(0.5 * RATE)
    audio = np.concatenate([np.zeros(lead, np.float32), speech, np.zeros(int(2.5 * RATE), np.float32)])
    audio = audio + rng.normal(0, 0.0005, len(audio)).astype(np.float32)
    taker = talk.TurnTaker(detector.listen, transcribe, gaps=gaps or talk.GAPS)
    step, end = int(TICK * RATE), lead + len(speech)
    found = None
    for start in range(0, len(audio), step):
        for event in taker.feed(audio[start:start + step]):
            if event[0] == "turn":
                found = ((min(start + step, len(audio)) - end) / RATE, event[1], event[2], list(taker.decisions))
                if not last:
                    return found
    return found or (None, taker.text, taker.kind, list(taker.decisions))


def drain(taker, limit=120.0):
    """Let a read that is still out finish and take its answer in: it must not run into the next clip's timing,
    and what the reader finished late must not be lost. Gives the events of that last feed()."""
    deadline = time.perf_counter() + limit
    while taker.reading and time.perf_counter() < deadline:
        time.sleep(0.01)
    return taker.feed(np.zeros(0, np.float32))


def fire_live(talk, detector, transcribe, speech, rng, hold=contextlib.nullcontext):
    """As fire_time, but in real time and with the read on its own thread, the way talk mode runs it.

    Seconds of wall clock from the end of `speech` to the turn (None: not within 2.5 s), what was heard, its
    kind, the seconds each read took, how many reads came back after the speaker had gone on, and the seconds
    from the end of `speech` to the moment the last read was back. `hold` wraps the whole clip (talk mode keeps
    the process above normal priority for the conversation)."""
    lead = int(0.5 * RATE)
    audio = np.concatenate([np.zeros(lead, np.float32), speech, np.zeros(int(2.5 * RATE), np.float32)])
    audio = audio + rng.normal(0, 0.0005, len(audio)).astype(np.float32)
    back = []

    def stamped(samples):
        try:
            return transcribe(samples)
        finally:
            back.append(time.perf_counter())

    taker = talk.TurnTaker(detector.listen, stamped, background=True)
    step, end = int(TICK * RATE), lead + len(speech)
    fired = heard = kind = None
    with hold():
        began = time.perf_counter()
        for start in range(0, len(audio), step):
            wait = began + min(start + step, len(audio)) / RATE - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            for event in taker.feed(audio[start:start + step]):
                if event[0] == "turn" and fired is None:
                    fired, heard, kind = time.perf_counter() - began - end / RATE, event[1], event[2]
            if fired is not None:
                break
        late = [event for event in drain(taker) if event[0] == "turn"]
    if fired is None:                                            # too late for the clip, but what was it?
        heard, kind = (late[0][1], late[0][2]) if late else (taker.text, taker.kind)
    taker.close()
    read_done = back[-1] - began - end / RATE if back else None
    return fired, heard, kind, list(taker.decisions), taker.stale, read_done


def reader(stt, urgent):
    def heard(audio):                                            # as keyit_speech.Speech.transcribe does it:
        with urgent():                                           # above-normal priority while the user waits
            out = stt.decode(audio)
        return out[0] if isinstance(out, tuple) else out
    return heard


def turns(talk, detector, stt, clips, log, urgent, load=None, hold=contextlib.nullcontext):
    rng = np.random.default_rng(7)
    heard = reader(stt, urgent)
    rows, decisions, live_decisions = [], [], []
    plain = {kind: 0.0 for kind in talk.GAPS}                    # fires as soon as the detector hears the end
    for label, group, texts in (("finished", "first", HELD_OUT_FINISHED),
                                ("unfinished", "first", HELD_OUT_UNFINISHED),
                                ("finished", "second", HELD_OUT_2_FINISHED),
                                ("unfinished", "second", HELD_OUT_2_UNFINISHED),
                                ("finished", "third", HELD_OUT_3_FINISHED),
                                ("unfinished", "third", HELD_OUT_3_UNFINISHED),
                                ("added_to", "first", HELD_OUT_ADDED_TO)):
        for text in texts:
            speech = clips.get(text, USER_VOICE)
            fired, read, kind, took = fire_time(talk, detector, heard, speech, rng)
            decisions += took
            mark = load.mark() if load else 0
            fired_live, read_live, kind_live, took_live, stale, read_done = fire_live(
                talk, detector, heard, speech, rng, hold)
            busy = load.since(mark) if load else None
            live_decisions += took_live
            earliest, *_ = fire_time(talk, detector, lambda audio: "okay then", speech, rng, gaps=plain, last=True)
            rows.append({"label": label, "list": group, "said": text, "heard": read, "kind": kind, "fired": fired,
                         "detector": earliest, "decide_s": [round(value, 3) for value in took],
                         "early": detector.min_silence,
                         "fired_live": None if fired_live is None else round(fired_live, 3),
                         "heard_live": read_live, "kind_live": kind_live, "stale": stale,
                         "live_decide_s": [round(value, 3) for value in took_live],
                         "read_done_live": None if read_done is None else round(read_done, 3),
                         "load_live_percent": busy["mean_percent"] if isinstance(busy, dict) else None})
            log(f"  {label:10} {kind or '-':10} fired {fired}  live {rows[-1]['fired_live']}  heard {read!r}")
    return rows, decisions, live_decisions


def said(talk, text):
    return [word.lower() for word in talk.TOKEN.findall(text)]


def repair(talk, detector, transcribe, clips, log):
    """The speaker pauses after something that sounds finished, then adds to it: is Buddy's question whole?

    Audio timeline, as in (a). One turn: Buddy waited. Two or more: it answered early, and the session asks
    again with the parts joined (keyit_talk.join_turns) - scored against the script."""
    rng = np.random.default_rng(11)
    lead, step = int(0.5 * RATE), int(TICK * RATE)
    rows = []
    for first, more in zip(HELD_OUT_ADDED_TO, CONTINUATIONS):
        script = said(talk, f"{first} {more}")
        one, two = clips.get(first, USER_VOICE), clips.get(more, USER_VOICE)
        for pause in PAUSES:
            audio = np.concatenate([np.zeros(lead, np.float32), one, np.zeros(int(pause * RATE), np.float32), two,
                                    np.zeros(int(2.5 * RATE), np.float32)])
            audio = audio + rng.normal(0, 0.0005, len(audio)).astype(np.float32)
            taker = talk.TurnTaker(detector.listen, transcribe)
            found = []
            for start in range(0, len(audio), step):
                at = (min(start + step, len(audio)) - lead - len(one)) / RATE
                found += [(at, event[1]) for event in taker.feed(audio[start:start + step]) if event[0] == "turn"]
            question = ""
            for _, text in found:
                question = talk.join_turns(question, text) if question else text
            got = said(talk, question)
            left, hit = list(got), 0
            for word in script:
                if word in left:
                    left.remove(word)
                    hit += 1
            rows.append({"said": f"{first} | {more}", "pause": pause, "turns": len(found),
                         "first_turn_at": round(found[0][0], 3) if found else None, "question": question,
                         "whole": got == script, "recall": round(hit / len(script), 3)})
            log(f"  repair {pause} s  turns {len(found)}  {question!r}")

    def summed(chosen):
        if not chosen:
            return {"n": 0, "whole": None, "recall": None}
        return {"n": len(chosen), "whole": round(sum(row["whole"] for row in chosen) / len(chosen), 3),
                "recall": round(statistics.fmean(row["recall"] for row in chosen), 3)}

    by_pause = {}
    for pause in PAUSES:
        mine = [row for row in rows if row["pause"] == pause]
        by_pause[str(pause)] = {"answered_early": round(sum(row["turns"] >= 2 for row in mine) / len(mine), 3),
                                "repaired": summed([row for row in mine if row["turns"] >= 2]),
                                "held": summed([row for row in mine if row["turns"] == 1])}
    heard_s = getattr(talk.TalkSession, "HEARD", None)
    return {"n": len(HELD_OUT_ADDED_TO), "rows": rows, "by_pause": by_pause, "heard_s": heard_s,
            "all": {"repaired": summed([row for row in rows if row["turns"] >= 2]),
                    "held": summed([row for row in rows if row["turns"] == 1])},
            "condition": f"The session only joins the parts if Buddy has been audible for less than {heard_s} s "
                         "when the speaker goes on; after that it is a new question. How soon Buddy is audible "
                         "depends on the model that answers and is not measured here."}


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

    for row in rows:                                             # the detector reports an end late, by its min
        row["detector_end"] = (row["detector"] or 0.0) - row.get("early", 0.3)   # silence: counted from there.
    buddy = measured(lambda row: row["fired"])

    def live(row):
        if "fired_live" in row:                                  # measured: real time, the read on its own thread
            return row["fired_live"]
        if row["fired"] is None:                                 # before round 3: the read ran inside the loop, so a
            return None                                          # turn could not end before the engine had answered
        decided = (row["detector"] or 0.0) + (row["decide_s"][-1] if row["decide_s"] else 0.0)
        return max(row["fired"], decided)

    def with_read(fire):                                         # a fixed pause has to know the words too:
        def fires(row):                                          # it answers when its pause is over and the
            pause = fire(row)                                    # same read, as measured, is back
            if pause is None:
                return None
            waited = max(pause, row.get("read_done_live") or 0.0)
            return waited if waited <= 2.5 else None
        return fires

    grid = {round(gap, 2): measured(fixed(gap)) for gap in np.arange(0.3, 1.65, 0.05)}
    same = min(grid, key=lambda gap: abs(grid[gap]["mean_delay_finished_s"] - buddy["mean_delay_finished_s"]))
    best = max(grid, key=lambda gap: grid[gap]["balanced_accuracy"])
    added = [row for row in rows if row["label"] == "added_to"]
    reads = {}
    if any(row.get("read_done_live") is not None for row in rows):
        waiting = {round(gap, 2): measured(with_read(fixed(gap))) for gap in np.arange(0.3, 1.65, 0.05)}
        top = max(waiting, key=lambda gap: waiting[gap]["balanced_accuracy"])
        reads = {"fixed_silence_0.7_with_read": waiting[0.7],
                 "fixed_silence_best_with_read": {"gap_s": top, **waiting[top]}}
    return {"buddy": buddy, **reads,
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
           "en_held_out": accuracy(HELD_OUT_FINISHED, HELD_OUT_UNFINISHED),
           "en_held_out_2": accuracy(HELD_OUT_2_FINISHED, HELD_OUT_2_UNFINISHED)}
    for language, (finished, unfinished) in OTHER_LANGUAGES.items():
        out[language] = accuracy(finished, unfinished)
    return out


def rules_speed(talk):
    texts = (DEV_FINISHED + DEV_UNFINISHED + HELD_OUT_FINISHED + HELD_OUT_UNFINISHED
             + HELD_OUT_2_FINISHED + HELD_OUT_2_UNFINISHED)
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

class Caught(logging.Handler):
    """What the voice logs instead of raising (its player thread has nobody to raise to)."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.errors = []

    def emit(self, record):
        error = record.exc_info[1] if record.exc_info else None
        self.errors.append(record.getMessage() + (f": {type(error).__name__}: {error}" if error else ""))


def output_device():
    try:
        import sounddevice
        device = sounddevice.query_devices(kind="output")
        return f"{device['name']} ({int(device['default_samplerate'])} Hz)"
    except Exception as error:
        return f"none ({type(error).__name__}: {error})"


def voice_reaction(voice, log):
    stamps = []
    voice.on_block = lambda samples, rate, at: stamps.append(time.monotonic())
    stops, pauses, resumes = [], [], []
    caught = Caught()
    logging.getLogger().addHandler(caught)

    def failed(why):
        log(f"  voice  not measured: {why}  {caught.errors}")
        return {"measured": False, "why": why, "error": " / ".join(dict.fromkeys(caught.errors)) or None,
                "device": output_device()}

    try:
        for _ in range(8):
            del stamps[:]
            voice.audio.put((voice.generation, np.zeros(24000 * 4, np.float32), 24000))
            deadline = time.monotonic() + 20
            while len(stamps) < 12 and time.monotonic() < deadline and not caught.errors:
                time.sleep(0.005)
            if len(stamps) < 12:
                return failed("the output device did not start")
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
        return failed(f"{type(error).__name__}: {error}")
    finally:
        voice.on_block = None
        logging.getLogger().removeHandler(caught)
    log(f"  voice  pause {spread(pauses)}  stop {spread(stops)}")
    return {"measured": True, "samples": "silence", "device": output_device(),
            "pause_to_silent_s": spread(pauses), "resume_to_sound_s": spread(resumes),
            "stop_to_idle_s": spread(stops), "rounds": len(stops)}
def report(result):
    turns_, speed, barge = result.get("turns"), result.get("decisions"), result.get("barge_in")
    engine, ahead, fix = result.get("engine", "whisper-en"), result.get("read_ahead"), result.get("repair")
    lines = ["# Talk mode bench", "", f"Run {result['when']} on {result['machine']}.", ""]
    if turns_:
        lines += [f"## (a) Turns: finished or not (English, real voice detector + {engine} on Kokoro speech)", "",
                  f"{turns_['counts']['finished']} finished and {turns_['counts']['unfinished']} unfinished "
                  f"utterances in {len(result.get('turns_by_list') or LISTS)} lists (see the table by list); pauses {PAUSES} s; a finished turn has to be taken within {BUDGET} s of silence. "
                  "Delays are on the audio timeline; compute time is in (b)."
                  + (" The row with compute is measured in real time: wall clock, the speech engine reading on its "
                     "own thread while the clip plays, as talk mode runs it." if ahead else ""), "",
                  "| end-pointer | finished taken in time | unfinished not cut | balanced accuracy | mean delay, finished |",
                  "|---|---|---|---|---|"]
        for name, key in (("Buddy (reads the words)", "buddy"),
                          ("Buddy, with compute under this load", "buddy_with_compute"),
                          ("fixed silence, same delay", "fixed_silence_same_delay"),
                          ("fixed silence 0.7 s", "fixed_silence_0.7"), ("fixed silence, its best gap", "fixed_silence_best"),
                          ("fixed silence 0.7 s, then waits for the same read (computed)", "fixed_silence_0.7_with_read"),
                          ("fixed silence, its best gap, then waits for the same read (computed)",
                           "fixed_silence_best_with_read")):
            if key not in turns_:
                continue
            row = turns_[key]
            gap = f" ({row['gap_s']} s)" if "gap_s" in row else ""
            lines.append(f"| {name}{gap} | {row['finished_in_time']:.0%} | {row['unfinished_held']:.0%} | "
                         f"{row['balanced_accuracy']:.1%} | {row['mean_delay_finished_s']} s |")
        if result.get("turns_by_list"):
            lines += ["", "| list | finished taken in time | unfinished not cut | balanced accuracy | "
                      "with compute | fixed silence, same delay | fixed silence, its best gap |",
                      "|---|---|---|---|---|---|---|"]
            for group, scored in result["turns_by_list"].items():
                lines.append(f"| {LISTS[group]} | {scored['buddy']['finished_in_time']:.0%} | "
                             f"{scored['buddy']['unfinished_held']:.0%} | {scored['buddy']['balanced_accuracy']:.1%} | "
                             f"{scored['buddy_with_compute']['balanced_accuracy']:.1%} | "
                             f"{scored['fixed_silence_same_delay']['balanced_accuracy']:.1%} | "
                             f"{scored['fixed_silence_best']['balanced_accuracy']:.1%} |")
        shares = lambda by_pause: ", ".join(f"{pause} s pause {share:.0%}" for pause, share in by_pause.items())
        lines += ["", f"Buddy, unfinished not cut, by pause length: {shares(turns_['buddy']['held_by_pause'])}.", "",
                  f"Sounds finished but the speaker adds to it ({turns_['counts']['added_to']} clips, not in the "
                  f"accuracy): answered before the speaker went on, {shares(turns_['added_to_cut_by_pause'])}.", ""]
        lines += ["Rules on typed text (no audio): " + ", ".join(
            f"{name} {row['balanced_accuracy']:.0%} (n={row['n']})" for name, row in result["text_only"].items()) + ".", ""]
    if fix:
        share = lambda value: "-" if value is None else f"{value:.0%}"
        lines += ["## (a2) Turn repair: the speaker adds to something that sounded finished", "",
                  f"{fix['n']} two-part questions ({engine}); part two starts after the pause. Whole = the question "
                  "Buddy ends up with has every scripted word in order; word recall = share of the scripted words "
                  "in it. Audio timeline.", "",
                  "| pause | answered before part two | then repaired: whole | word recall | held as one turn: whole "
                  "| word recall |", "|---|---|---|---|---|---|"]
        for pause, row in fix["by_pause"].items():
            lines.append(f"| {pause} s | {row['answered_early']:.0%} (n={row['repaired']['n']}) | "
                         f"{share(row['repaired']['whole'])} | {share(row['repaired']['recall'])} | "
                         f"{share(row['held']['whole'])} | {share(row['held']['recall'])} |")
        lines += ["", fix["condition"], ""]
    if speed:
        lines += ["## (b) One decision", "", f"CPU load during the run: {speed['load']}.", "",
                  "| what | p50 | p95 | n |", "|---|---|---|---|",
                  f"| rules alone, given the words (what Jev is given) | {speed['rules_ms']['p50']} ms | "
                  f"{speed['rules_ms']['p95']} ms | {speed['rules_ms']['n']} |"]
        if speed.get("stt_and_rules_s"):
            lines.append(f"| speech engine ({engine}) + rules | {speed['stt_and_rules_s']['p50']} s | "
                         f"{speed['stt_and_rules_s']['p95']} s | {speed['stt_and_rules_s']['n']} |")
        if ahead and ahead.get("live_decisions_s"):
            lines.append(f"| the same in the real-time pass | {ahead['live_decisions_s']['p50']} s | "
                         f"{ahead['live_decisions_s']['p95']} s | {ahead['live_decisions_s']['n']} |")
        if ahead:
            lines += ["", f"Read-ahead: the read starts {ahead['early_s']} s into a pause and the shortest gap is "
                      f"{ahead['shortest_gap_s']} s, so the first {ahead['hidden_s']} s of a read cost the speaker "
                      f"nothing. {ahead['stale_reads']} of {ahead['reads']} reads were thrown away because the "
                      "speaker had gone on."]
            if ahead.get("finished_live_s"):
                lines += [f"Finished turns in real time: taken p50 {ahead['finished_live_s']['p50']} s, p95 "
                          f"{ahead['finished_live_s']['p95']} s after the last sound; that is p50 "
                          f"{ahead['extra_wait_finished_s']['p50']} s, p95 {ahead['extra_wait_finished_s']['p95']} s "
                          f"later than on the audio timeline (n={ahead['finished_live_s']['n']})."]
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
        lines += ["## (d) Voice on the real output device (silent samples)", ""]
        if voice.get("measured") is False:
            lines += [f"Not measured: {voice.get('why')}."
                      + (f" The voice logged: {voice['error']}." if voice.get("error") else "")
                      + (f" Default output device: {voice['device']}." if voice.get("device") else ""), ""]
        else:
            lines += [f"Device: {voice.get('device')}; {voice.get('rounds')} rounds; the samples played were "
                      f"{voice.get('samples')}, so nothing was audible.", "",
                      "| what | p50 | p95 | max | mean | n |", "|---|---|---|---|---|---|"]
            for label, key in (("pause requested to silent", "pause_to_silent_s"),
                               ("resume requested to samples flowing", "resume_to_sound_s"),
                               ("stop requested to device idle", "stop_to_idle_s")):
                spread = voice.get(key)
                if spread:
                    lines.append(f"| {label} | {spread['p50']} s | {spread['p95']} s | {spread['max']} s | "
                                 f"{spread['mean']} s | {spread['n']} |")
            lines.append("")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--code", default=str(BENCH.parent / "keyit"))
    parser.add_argument("--out", default=str(BENCH / "results_talk"))
    parser.add_argument("--cache", default=str(BENCH / "results_talk" / "clips"))
    parser.add_argument("--only", default="abcd", help="parts to run, e.g. 'c'")
    parser.add_argument("--engine", default="whisper-en", help="keyit_speech engine that reads the turns")
    parser.add_argument("--hold", action="store_true",
                        help="real-time pass: above-normal priority for the whole clip, not only during a read")
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

    def save():                                                  # after every part: a late failure loses nothing
        result_path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
        (out / "talk_turns.md").write_text(report(result), encoding="utf-8", newline="\n")

    voice = keyit_voice.NaturalVoice()
    voice.ready.wait(600)
    if voice.failed:
        raise SystemExit(f"Kokoro did not load: {voice.failed}")
    clips = Clips(args.cache, voice, keyit_speech.resample)
    if "a" in args.only or "b" in args.only:
        log("(a) turns")
        for text in (HELD_OUT_FINISHED + HELD_OUT_UNFINISHED + HELD_OUT_2_FINISHED + HELD_OUT_2_UNFINISHED
                     + HELD_OUT_3_FINISHED + HELD_OUT_3_UNFINISHED + HELD_OUT_ADDED_TO
                     + CONTINUATIONS):                                                # synthesize before timing
            clips.get(text, USER_VOICE)
        stt = keyit_speech.SherpaSpeech(args.engine)
        stt.warm_up()
        detector = keyit_speech.VoiceDetector(min_silence=talk.EARLY)                # as talk mode builds it
        result["engine"] = args.engine
        mark = load.mark()
        hold = keyit_speech.urgent if args.hold else contextlib.nullcontext
        rows, decisions, live_decisions = turns(talk, detector, stt, clips, log, keyit_speech.urgent, load, hold)
        both = [row for row in rows if row["label"] == "finished" and row["fired"] is not None
                and row["fired_live"] is not None]
        result["read_ahead"] = {
            "early_s": talk.EARLY, "shortest_gap_s": min(talk.GAPS.values()),
            "hidden_s": round(min(talk.GAPS.values()) - talk.EARLY, 3),
            "stale_reads": sum(row["stale"] for row in rows), "reads": len(live_decisions),
            "live_decisions_s": spread(live_decisions) if live_decisions else None,
            "extra_wait_finished_s": spread([row["fired_live"] - row["fired"] for row in both]) if both else None,
            "finished_live_s": spread([row["fired_live"] for row in both]) if both else None,
            "read_back_finished_s": spread([row["read_done_live"] for row in rows if row["label"] == "finished"
                                            and row["read_done_live"] is not None]),
            "priority": "above normal for the whole clip" if args.hold else "above normal during a read"}
        result["turn_rows"] = rows
        result["turns"] = score(rows)
        result["turns_by_list"] = {group: score([row for row in rows
                                                 if row["list"] == group and row["label"] != "added_to"])
                                   for group in LISTS}
        result["decisions"] = {"rules_ms": rules_speed(talk), "stt_and_rules_s": spread(decisions),
                               "load": load.since(mark)}
        log(json.dumps(result["turns"], indent=1))
        save()
        log("(a2) turn repair")
        result["repair"] = repair(talk, detector, reader(stt, keyit_speech.urgent), clips, log)
        save()
    if "c" in args.only:
        log("(c) barge-in")
        result["barge_in"] = barge_in(talk, clips, log)
    if "d" in args.only:
        log("(d) voice")
        result["voice"] = voice_reaction(voice, log)
    if clips.seconds:
        result["synthesis_s_per_clip"] = spread(clips.seconds)
    save()
    log(report(result))


if __name__ == "__main__":
    main()
