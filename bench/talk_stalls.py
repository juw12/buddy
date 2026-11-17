"""Talk mode bench: why does one read in twenty take seconds instead of tenths of a second?

    python bench/talk_stalls.py --cache <folder with the clips of bench/talk_turns.py> --reads 200
    python bench/talk_stalls.py --cache <the same> --live --reads 30 --variants live,plain,two-thread,one-thread

bench/talk_turns.py found finished turns that were never answered because the speech engine took 2.6 to
4.7 s over a clip it normally reads in 0.4 s. This script reads the same clips and writes down, for every
read, where the time went.

Without --live: one read, then a few seconds of nothing, at above-normal priority, on the main thread.

    wall_s        seconds from asking to having the text
    cpu_s         processor seconds this process used meanwhile (all its threads)
    hard_faults   pages of this process that Windows had to fetch back from the disk meanwhile
    pages_in      pages the whole PC read back from the disk meanwhile
    load          how busy all processors were, in percent
    trimmed_mb    how much of this process Windows had taken out of memory since the read before

    variants: shipped (the engine as talk mode uses it), one-thread (the same engine on one thread)

With --live: the clip is fed in real time, as talk mode hears it, and the read happens where talk mode does
it. Each variant in turn on the same clip, so they meet the same PC:

    live          talk mode as shipped: voice detector on the main thread, read on the reader thread
    no-detector   the same, but the main thread is told where the speech ends and computes nothing
    one-thread    as live, the speech engine on one thread
    two-thread    as live, the speech engine on two threads
    plain         the control: the same clip read on the main thread, no detector, no reader thread

    and per read, on top of the fields above:
    speed             "% Processor Performance" of the PC during the read: 100 is the nominal clock, more is
                      turbo, far less means the processor was being held back
    kernel_s, user_s  cpu_s split into time inside Windows and time in the program
    own_cpu_s         processor seconds of the thread that waited for the text
    soft_faults       page faults that needed no disk
    tick_late_max_s   how late the 30 ms audio tick ran at worst

How to read a slow read: cpu_s small and hard_faults large -> it waited for memory from the disk; cpu_s
small and load at 100 % -> it waited for a processor; cpu_s LARGER than in a quick read and speed far below
100 -> the processor itself ran slowly.

What it found on the PC it was written on (Core Ultra 7 258V laptop, 92-96 % of memory in use, 2026-11-16):
the third case. Every read over 1.5 s ran at reduced processor speed (18-71 %; six of the nine at 18-19 %),
every read below 50 % speed was a stall, hard faults were about zero in stalls and quick reads alike, and the
control (plain) stalls the same way. The firmware holds the processor back for seconds at a time when the
package is near 93 degrees C; Windows reports no limit of its own. That is not something Buddy can fix; fewer
threads make it worse (one-thread: 11 of 30 reads over 1.0 s). Numbers: bench/results_talk/talk_stalls_*.json.

Windows only: the counters come from NtQuerySystemInformation and the performance data helper.
"""
import argparse
import ctypes
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

import numpy as np

BENCH = Path(__file__).resolve().parent
RATE = 16000
STALL = 1.0                 # s: a read this slow costs a finished turn its 1.0 s budget
TICK = 0.03                 # s, keyit_talk.TalkSession.TICK
PAD = 0.25                  # s of audio around the speech, as keyit_talk.TurnTaker.PAD


# --- counters ---------------------------------------------------------------------------------------------
class Counters:
    """Hard page faults of this process and pages read in by the whole PC (cumulative counts)."""

    PROCESS_INFORMATION = 5                                      # SystemProcessInformation
    # SYSTEM_PROCESS_INFORMATION on 64-bit Windows: byte offsets of the fields that are read
    NEXT, THREADS, HARD_FAULTS, PROCESS_ID = 0, 4, 16, 80

    def __init__(self):
        self.pid = os.getpid()
        self.ntdll = ctypes.WinDLL("ntdll")
        self.size = 1 << 20
        self.pdh = ctypes.WinDLL("pdh")
        self.query, self.counter = ctypes.c_void_p(), ctypes.c_void_p()
        self.pdh.PdhOpenQueryW(None, ctypes.c_void_p(0), ctypes.byref(self.query))
        self.pdh.PdhAddEnglishCounterW(self.query, "\\Memory\\Pages Input/sec", ctypes.c_void_p(0),
                                       ctypes.byref(self.counter))
        self.speed_query = None

    def process(self):
        """(hard faults so far, threads) of this process."""
        while True:
            buffer = ctypes.create_string_buffer(self.size)
            needed = ctypes.c_ulong()
            status = self.ntdll.NtQuerySystemInformation(self.PROCESS_INFORMATION, buffer, self.size,
                                                         ctypes.byref(needed))
            if status == 0:
                break
            self.size = max(self.size * 2, needed.value + (1 << 16))
        raw, offset = buffer.raw, 0
        while True:
            if int.from_bytes(raw[offset + self.PROCESS_ID:offset + self.PROCESS_ID + 8], "little") == self.pid:
                return (int.from_bytes(raw[offset + self.HARD_FAULTS:offset + self.HARD_FAULTS + 4], "little"),
                        int.from_bytes(raw[offset + self.THREADS:offset + self.THREADS + 4], "little"))
            step = int.from_bytes(raw[offset + self.NEXT:offset + self.NEXT + 4], "little")
            if not step:
                raise RuntimeError("this process is not in the system's process list")
            offset += step

    def speed(self):
        """Processor speed since the last call, in percent of the nominal speed (below 100: slowed down to
        save power or to cool off; above: turbo). None at the first call."""
        class Formatted(ctypes.Structure):
            _fields_ = [("status", ctypes.c_ulong), ("value", ctypes.c_double)]

        if self.speed_query is None:
            self.speed_query, self.speed_counter = ctypes.c_void_p(), ctypes.c_void_p()
            self.pdh.PdhOpenQueryW(None, ctypes.c_void_p(0), ctypes.byref(self.speed_query))
            self.pdh.PdhAddEnglishCounterW(self.speed_query,
                                           "\\Processor Information(_Total)\\% Processor Performance",
                                           ctypes.c_void_p(0), ctypes.byref(self.speed_counter))
        self.pdh.PdhCollectQueryData(self.speed_query)
        found = Formatted()
        status = self.pdh.PdhGetFormattedCounterValue(self.speed_counter, 0x200 | 0x8000, None,
                                                      ctypes.byref(found))        # a double, not capped at 100
        return round(found.value, 1) if status == 0 and found.status in (0, 1) else None

    def thread_times(self):
        """(kernel s, user s) of the calling thread so far."""
        kernel = ctypes.WinDLL("kernel32")
        kernel.GetCurrentThread.restype = ctypes.c_void_p
        kernel.GetThreadTimes.argtypes = [ctypes.c_void_p] + [ctypes.c_void_p] * 4
        times = [ctypes.c_ulonglong() for _ in range(4)]
        kernel.GetThreadTimes(kernel.GetCurrentThread(), *[ctypes.byref(value) for value in times])
        return times[2].value / 1e7, times[3].value / 1e7

    def pages_in(self):
        class Raw(ctypes.Structure):
            _fields_ = [("status", ctypes.c_ulong), ("low", ctypes.c_ulong), ("high", ctypes.c_ulong),
                        ("first", ctypes.c_longlong), ("second", ctypes.c_longlong), ("multi", ctypes.c_ulong)]

        raw = Raw()
        self.pdh.PdhCollectQueryData(self.query)
        self.pdh.PdhGetRawCounterValue(self.counter, None, ctypes.byref(raw))
        return int(raw.first)


# --- the reads --------------------------------------------------------------------------------------------
def spread(values, digits=3):
    values = sorted(values)
    if not values:
        return None
    pick = lambda share: values[min(len(values) - 1, int(round(share * (len(values) - 1))))]
    return {"n": len(values), "p50": round(pick(0.5), digits), "p95": round(pick(0.95), digits),
            "max": round(values[-1], digits), "mean": round(statistics.fmean(values), digits)}


def one_read(decode, audio, counters, me, psutil):
    faults, _ = counters.process()
    pages, cpu = counters.pages_in(), me.cpu_times()
    psutil.cpu_percent(None)
    started = time.perf_counter()
    text = decode(audio)
    wall = time.perf_counter() - started
    load = psutil.cpu_percent(None)
    spent = me.cpu_times()
    return {"wall_s": round(wall, 3), "cpu_s": round(spent.user + spent.system - cpu.user - cpu.system, 3),
            "hard_faults": counters.process()[0] - faults, "pages_in": counters.pages_in() - pages,
            "load": load, "heard": text}


def summary(rows):
    stalls = [row for row in rows if row["wall_s"] > STALL]
    quick = [row for row in rows if row["wall_s"] <= STALL]
    mean = lambda chosen, key: round(statistics.fmean(row[key] for row in chosen), 3) if chosen else None
    return {"reads": len(rows), "wall_s": spread([row["wall_s"] for row in rows]),
            "stalls": len(stalls), "stall_share": round(len(stalls) / len(rows), 3) if rows else None,
            "stalls_with_hard_faults": sum(row["hard_faults"] > 0 for row in stalls),
            "stalls_at_full_load": sum(row["load"] >= 99.0 for row in stalls),
            "in_stalls": {key: mean(stalls, key) for key in ("wall_s", "cpu_s", "hard_faults", "pages_in", "load",
                                                              "trimmed_mb")},
            "in_quick_reads": {key: mean(quick, key) for key in ("wall_s", "cpu_s", "hard_faults", "pages_in",
                                                                  "load", "trimmed_mb")}}


# --- the reads as talk mode makes them ---------------------------------------------------------------------
class Scripted:
    """Stands in for the voice detector: it is told where the speech is, so nothing but the reader thread
    computes. Same answers as keyit_speech.Listening gives keyit_talk.TurnTaker (hear / since)."""

    def __init__(self, first, last, wait):
        self.first, self.last, self.wait = first, last, wait
        self.heard, self.told = 0, False

    @property
    def since(self):
        return self.first if self.first <= self.heard < self.last + self.wait else None

    def hear(self, audio16k):
        self.heard += len(audio16k)
        if self.told or self.heard < self.last + self.wait:
            return []
        self.told = True
        return [(self.first, self.last)]


def staged(engine, urgent, me, marks, counters):
    """keyit_speech.SherpaSpeech.decode inside urgent(), as talk mode calls it, with a clock after each step."""
    def heard(audio):
        clock = time.perf_counter
        counters.speed()
        mark = {"asked": clock(), "cpu": me.cpu_times(), "own_cpu": time.thread_time(),
                "own": counters.thread_times(), "faults": me.memory_info().num_page_faults}
        with urgent():
            mark["priority"] = clock()
            with engine.busy:
                mark["lock"] = clock()
                stream = engine.recognizer.create_stream()
                stream.accept_waveform(RATE, audio)
                mark["stream"] = clock()
                engine.recognizer.decode_stream(stream)
                mark["decoded"] = clock()
                text = stream.result.text.strip()
        mark["back"] = clock()
        mark["cpu_after"], mark["own_cpu_after"] = me.cpu_times(), time.thread_time()
        mark["own_after"], mark["faults_after"] = counters.thread_times(), me.memory_info().num_page_faults
        mark["speed"] = counters.speed()
        marks.append(mark)
        return text
    return heard


def live_read(talk, new_listening, engine, speech, rng, hold, urgent, counters, me, psutil):
    """One clip in real time through keyit_talk.TurnTaker with the read on its thread (bench/talk_turns.py,
    fire_live), and where the time of each read went."""
    lead = int(0.5 * RATE)
    audio = np.concatenate([np.zeros(lead, np.float32), speech, np.zeros(int(2.5 * RATE), np.float32)])
    audio = audio + rng.normal(0, 0.0005, len(audio)).astype(np.float32)
    end = lead + len(speech)
    marks, ticks = [], []
    listening = new_listening(lead, end)
    taker = talk.TurnTaker(listening, staged(engine, urgent, me, marks, counters), background=True)
    step = int(TICK * RATE)
    faults, pages = counters.process()[0], counters.pages_in()
    psutil.cpu_percent(None)
    fired = None
    with hold():
        began = time.perf_counter()
        for start in range(0, len(audio), step):
            due = began + min(start + step, len(audio)) / RATE
            wait = due - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            woke = time.perf_counter()
            events = taker.feed(audio[start:start + step])
            ticks.append((woke, woke - due, time.perf_counter() - woke))
            if any(event[0] == "turn" for event in events):
                fired = time.perf_counter() - began - end / RATE
                break
        deadline = time.perf_counter() + 120.0
        while taker.reading and time.perf_counter() < deadline:
            time.sleep(0.01)
        taker.feed(np.zeros(0, np.float32))
    taker.close()
    load = psutil.cpu_percent(None)
    shared = {"hard_faults": counters.process()[0] - faults, "pages_in": counters.pages_in() - pages, "load": load,
              "fired_s": None if fired is None else round(fired, 3), "reads_in_clip": len(marks)}
    return [{**timed(mark, ticks), "asked_after_speech_s": round(mark["asked"] - began - end / RATE, 3), **shared}
            for mark in marks]


def timed(mark, ticks=()):
    """One read's marks as a row: where the wall time went, and what kind of processor time it was."""
    during = [tick for tick in ticks if mark["asked"] <= tick[0] <= mark["back"]]
    spent, before = mark["cpu_after"], mark["cpu"]
    return {
        "wall_s": round(mark["back"] - mark["asked"], 3),
        "priority_s": round(mark["priority"] - mark["asked"], 3),
        "lock_s": round(mark["lock"] - mark["priority"], 3),
        "stream_s": round(mark["stream"] - mark["lock"], 3),
        "decode_s": round(mark["decoded"] - mark["stream"], 3),
        "result_s": round(mark["back"] - mark["decoded"], 3),
        "cpu_s": round(spent.user + spent.system - before.user - before.system, 3),
        "kernel_s": round(spent.system - before.system, 3),
        "user_s": round(spent.user - before.user, 3),
        "own_cpu_s": round(mark["own_cpu_after"] - mark["own_cpu"], 3),
        "own_kernel_s": round(mark["own_after"][0] - mark["own"][0], 3),
        "soft_faults": mark["faults_after"] - mark["faults"],
        "speed": mark["speed"] if mark["speed"] is not None else 0.0,
        "ticks_during": len(during),
        "tick_late_max_s": round(max((tick[1] for tick in during), default=0.0), 3),
        "feed_max_s": round(max((tick[2] for tick in during), default=0.0), 3)}


def plain_read(engine, speech, rng, urgent, counters, me, psutil):
    """The control: the same clip after the same wait, read on the main thread with nothing else going on."""
    pad = np.zeros(int(PAD * RATE), np.float32)
    audio = np.concatenate([pad, speech, pad])
    audio = audio + rng.normal(0, 0.0005, len(audio)).astype(np.float32)
    marks = []
    faults, pages = counters.process()[0], counters.pages_in()
    with urgent():
        time.sleep(0.5 + len(speech) / RATE + 0.1)               # as long as the live read waits for its clip
        psutil.cpu_percent(None)
        staged(engine, urgent, me, marks, counters)(audio)
        load = psutil.cpu_percent(None)
        time.sleep(1.0)
    return [{**timed(marks[0]), "asked_after_speech_s": 0.1, "hard_faults": counters.process()[0] - faults,
             "pages_in": counters.pages_in() - pages, "load": load, "fired_s": None, "reads_in_clip": 1}]


LIVE_KEYS = ("wall_s", "priority_s", "lock_s", "stream_s", "decode_s", "result_s", "cpu_s", "kernel_s", "user_s",
             "own_cpu_s", "own_kernel_s", "soft_faults", "speed", "tick_late_max_s", "feed_max_s", "hard_faults",
             "pages_in", "load")
THREADS_OF = {"one-thread": 1, "two-thread": 2}                  # every other variant: as shipped


def live_summary(rows):
    stalls = [row for row in rows if row["wall_s"] > STALL]
    quick = [row for row in rows if row["wall_s"] <= STALL]
    mean = lambda chosen, key: round(statistics.fmean(row[key] for row in chosen), 3) if chosen else None
    return {"reads": len(rows), "wall_s": spread([row["wall_s"] for row in rows]),
            "stalls": len(stalls), "stall_share": round(len(stalls) / len(rows), 3) if rows else None,
            "stalls_with_hard_faults": sum(row["hard_faults"] > 0 for row in stalls),
            "in_stalls": {key: mean(stalls, key) for key in LIVE_KEYS},
            "in_quick_reads": {key: mean(quick, key) for key in LIVE_KEYS}}


def live(args, log):
    """--live: clip after clip, each variant in turn on the same clip.

        live          talk mode as shipped: voice detector on the main thread, read on the reader thread
        no-detector   the same, but the main thread is told where the speech ends and computes nothing
        one-thread    as live, the speech engine on one thread
        two-thread    as live, the speech engine on two threads
        plain         the control: the same clip read on the main thread, no detector, no reader thread
    """
    import psutil

    import keyit_speech
    import keyit_talk as talk
    import talk_turns

    clips = talk_turns.Clips(args.cache, None, keyit_speech.resample)
    texts = talk_turns.HELD_OUT_FINISHED + talk_turns.HELD_OUT_2_FINISHED + talk_turns.HELD_OUT_3_FINISHED
    rng = np.random.default_rng(7)
    names = [name.strip() for name in args.variants.split(",") if name.strip()]
    shipped_threads = keyit_speech.THREADS
    engines = {}
    for threads in sorted({THREADS_OF.get(name, shipped_threads) for name in names}):
        keyit_speech.THREADS = threads
        engines[threads] = keyit_speech.SherpaSpeech(args.engine)
    keyit_speech.THREADS = shipped_threads
    ballast = None
    if args.ballast:                                             # bench/talk_turns.py has Buddy's voice loaded too
        import keyit_voice
        ballast = keyit_voice.NaturalVoice()
        ballast.ready.wait(600)
    detector = keyit_speech.VoiceDetector(min_silence=talk.EARLY)
    counters, me = Counters(), psutil.Process()
    wait = int(talk.EARLY * RATE)
    rows = {name: [] for name in names}
    memory = psutil.virtual_memory()
    log(f"{len(texts)} clips, {args.reads} clips per variant, memory {memory.percent} % used, "
        f"process {me.memory_info().rss / 2 ** 20:.0f} MB")
    for index in range(args.reads):
        text = texts[index % len(texts)]
        speech = clips.get(text, talk_turns.USER_VOICE)
        order = names[index % len(names):] + names[:index % len(names)]
        for name in order:
            engine = engines[THREADS_OF.get(name, shipped_threads)]
            if name == "no-detector":
                new_listening = lambda lead, end: (lambda: Scripted(lead, end, wait))
            else:
                new_listening = lambda lead, end: detector.listen
            if name == "plain":
                found = plain_read(engine, speech, rng, keyit_speech.urgent, counters, me, psutil)
            else:
                found = live_read(talk, new_listening, engine, speech, rng, keyit_speech.urgent,
                                  keyit_speech.urgent, counters, me, psutil)
            for row in found:
                row.update({"variant": name, "clip": text})
                rows[name].append(row)
                if row["wall_s"] > STALL:
                    log(f"  stall {name:12} {row['wall_s']} s: decode {row['decode_s']}  cpu {row['cpu_s']} "
                        f"(kernel {row['kernel_s']}, user {row['user_s']})  own cpu {row['own_cpu_s']} "
                        f"(kernel {row['own_kernel_s']})  soft faults {row['soft_faults']}  hard faults "
                        f"{row['hard_faults']}  speed {row['speed']} %  tick late {row['tick_late_max_s']}  "
                        f"load {row['load']} %")
        if index % 10 == 9:
            log(f"{index + 1} clips: " + ", ".join(
                f"{name} {sum(row['wall_s'] > STALL for row in rows[name])} stalls in {len(rows[name])} reads"
                for name in names))
    return {"when": time.strftime("%Y-%m-%d %H:%M"), "mode": "live", "engine": args.engine, "stall_s": STALL,
            "machine": f"{platform.processor() or platform.machine()}, {os.cpu_count()} logical CPUs, "
                       f"{platform.system()} {platform.release()}",
            "memory": {"total_gb": round(memory.total / 2 ** 30, 1), "used_percent_at_start": memory.percent,
                       "used_percent_at_end": psutil.virtual_memory().percent},
            "threads_shipped": shipped_threads, "voice_loaded": bool(ballast),
            "variants": {name: live_summary(rows[name]) for name in names}, "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--code", default=str(BENCH.parent / "keyit"))
    parser.add_argument("--out", default=str(BENCH / "results_talk"))
    parser.add_argument("--cache", default=str(BENCH / "results_talk" / "clips"))
    parser.add_argument("--engine", default="whisper-en")
    parser.add_argument("--reads", type=int, default=120, help="reads per variant")
    parser.add_argument("--variants", default="shipped,one-thread")
    parser.add_argument("--name", default="talk_stalls", help="result file, without .json")
    parser.add_argument("--gap", type=float, nargs=2, default=(1.5, 4.0), help="s of nothing between two reads")
    parser.add_argument("--live", action="store_true",
                        help="the reads as talk mode makes them: in real time, on the reader thread")
    parser.add_argument("--ballast", action="store_true", help="--live: with Buddy's voice loaded as well")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Windows only")
    sys.path.insert(0, args.code)
    sys.path.insert(0, str(BENCH))
    if args.live:
        if args.variants == "shipped,one-thread":
            args.variants = "live,no-detector,one-thread"
        result = live(args, lambda text: print(text, flush=True))
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{args.name}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8",
                                              newline="\n")
        print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=1), flush=True)
        return
    import psutil

    import keyit_speech
    import talk_turns

    log = lambda text: print(text, flush=True)
    clips = talk_turns.Clips(args.cache, None, keyit_speech.resample)        # cached clips only: no voice loaded
    texts = talk_turns.HELD_OUT_FINISHED + talk_turns.HELD_OUT_2_FINISHED + talk_turns.HELD_OUT_3_FINISHED
    rng = np.random.default_rng(7)
    pad = np.zeros(int(PAD * RATE), np.float32)
    audio = []
    for text in texts:
        speech = np.concatenate([pad, clips.get(text, talk_turns.USER_VOICE), pad])
        audio.append(speech + rng.normal(0, 0.0005, len(speech)).astype(np.float32))

    names = [name.strip() for name in args.variants.split(",") if name.strip()]
    engines = {}
    shipped_threads = keyit_speech.THREADS
    for name in names:
        keyit_speech.THREADS = 1 if name == "one-thread" else shipped_threads
        engines[name] = keyit_speech.SherpaSpeech(args.engine)
    keyit_speech.THREADS = shipped_threads
    counters, me = Counters(), psutil.Process()
    _, threads = counters.process()
    assert threads == me.num_threads(), (threads, me.num_threads())          # the offsets above are right
    rows = {name: [] for name in names}
    memory = psutil.virtual_memory()
    log(f"{len(audio)} clips, {args.reads} reads per variant, memory {memory.percent} % used")
    with keyit_speech.urgent():                                              # as talk mode: for the conversation
        for index in range(args.reads):
            order = names[index % len(names):] + names[:index % len(names)]
            for name in order:
                before = me.memory_info().rss
                time.sleep(float(rng.uniform(*args.gap)))
                trimmed = (before - me.memory_info().rss) / 2 ** 20
                row = one_read(lambda samples: engines[name].decode(samples)[0], audio[index % len(audio)],
                               counters, me, psutil)
                row.update({"variant": name, "clip": texts[index % len(audio)], "trimmed_mb": round(trimmed, 1),
                            "available_gb": round(psutil.virtual_memory().available / 2 ** 30, 2)})
                rows[name].append(row)
                if row["wall_s"] > STALL:
                    log(f"  stall {name:10} {row['wall_s']} s  cpu {row['cpu_s']} s  hard faults "
                        f"{row['hard_faults']}  pages in {row['pages_in']}  load {row['load']} %")
            if index % 10 == 9:
                log(f"{index + 1} reads: " + ", ".join(
                    f"{name} {sum(row['wall_s'] > STALL for row in rows[name])} stalls" for name in names))
    result = {"when": time.strftime("%Y-%m-%d %H:%M"), "engine": args.engine, "stall_s": STALL,
              "machine": f"{platform.processor() or platform.machine()}, {os.cpu_count()} logical CPUs, "
                         f"{platform.system()} {platform.release()}",
              "memory": {"total_gb": round(memory.total / 2 ** 30, 1), "used_percent_at_start": memory.percent,
                         "used_percent_at_end": psutil.virtual_memory().percent},
              "gap_s": list(args.gap), "threads_shipped": shipped_threads,
              "variants": {name: summary(rows[name]) for name in names}, "rows": rows}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.name}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8",
                                          newline="\n")
    log(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=1))


if __name__ == "__main__":
    main()
