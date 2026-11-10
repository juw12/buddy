"""Unpacking a downloaded speech model: the way of Buddy 1.2.0 against the way of this code.

    python bench/unpack_time.py --archives <folder with the .tar.bz2 files> --out bench/results_r2/unpack_time.json

A model arrives as a .tar.bz2 archive. Buddy 1.2.0 listed the files in it and unpacked them afterwards, which
decompresses the archive twice. This code unpacks while it reads, once, into a folder that only gets the
model's name when it is complete. Here both ways unpack the same real archives, each time into an empty
folder, and the files that come out are compared byte for byte. Which way goes first alternates from round
to round, so slow drift on a shared PC cancels out. The archives are copied first and only the copies are
read, so nothing in --archives is touched.
"""
import argparse
import hashlib
import json
import os
import shutil
import statistics
import sys
import tarfile
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH))

from latency_hold import processor_times  # noqa: E402

WAYS = ("as 1.2.0 did", "as shipped")


def parse():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--code", default=str(BENCH.parent), help="the Buddy source tree to measure")
    parser.add_argument("--archives", required=True, help="folder that holds the downloaded .tar.bz2 files")
    parser.add_argument("--engines", nargs="+", default=["whisper-en", "dolphin"],
                        help="the engines whose models are unpacked")
    parser.add_argument("--rounds", type=int, default=2, help="times each way unpacks each archive")
    parser.add_argument("--scratch", help="folder to unpack into, removed afterwards (default: next to --out)")
    parser.add_argument("--out", required=True)
    return parser.parse_args()


def as_1_2_0(archive, folder, keep):
    """The unpack step of Buddy 1.2.0, word for word: download() in keyit/keyit_speech.py at the tag v1.2.0."""
    with tarfile.open(archive) as tar:
        members = [m for m in tar.getmembers() if not m.name.startswith(("/", "..")) and ".." not in m.name
                   and (keep is None or m.isdir() or m.name.endswith(keep))]
        try:
            tar.extractall(folder, members=members, filter="data")
        except TypeError:                                   # Python < 3.12
            tar.extractall(folder, members=members)


def unpacked(folder):
    """{path below the model's own folder: (bytes, SHA-256)} of every file that was unpacked into folder."""
    found = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            below = Path(*path.relative_to(folder).parts[1:]).as_posix()
            found[below] = (len(data), hashlib.sha256(data).hexdigest())
    return found


def timed(unpack):
    """(seconds one unpack took, share of the PC's processor time that was in use meanwhile)."""
    before, started = processor_times(), time.perf_counter()
    unpack()
    seconds = time.perf_counter() - started
    after = processor_times()
    return seconds, 1 - (after[0] - before[0]) / max(after[1] - before[1], 1)


def measure(speech, engine, archives, scratch, rounds):
    info = speech.ENGINES[engine]
    name, keep = info["archive"], info.get("keep")
    archive = scratch / (name + ".tar.bz2")
    shutil.copyfile(Path(archives) / archive.name, archive)
    ways = {
        "as 1.2.0 did": lambda folder: as_1_2_0(archive, folder, keep),
        "as shipped": lambda folder: speech._unpack(archive, folder, name, keep),
    }
    rows, results = [], []
    for number in range(rounds):
        for way in WAYS if number % 2 == 0 else WAYS[::-1]:
            folder = scratch / f"{engine}-{number}-{WAYS.index(way)}"
            folder.mkdir()
            seconds, pc_busy = timed(lambda: ways[way](folder))
            results.append(unpacked(folder))
            shutil.rmtree(folder)
            rows.append({"round": number, "way": way, "seconds": round(seconds, 2), "pc_busy": round(pc_busy, 3)})
            print(f"{engine}, round {number}, {way}: {seconds:.1f} s, PC {pc_busy:.0%} busy", flush=True)
    archive.unlink()
    files = results[0]
    return {
        "label": info["label"],
        "archive": archive.name,
        "archive_mb": round((Path(archives) / archive.name).stat().st_size / 1e6, 1),
        "kept": list(keep) if keep else None,
        "files": len(files),
        "unpacked_mb": round(sum(size for size, _ in files.values()) / 1e6, 1),
        "same_files": bool(files) and all(other == files for other in results),
        "pc_busy": round(statistics.mean(row["pc_busy"] for row in rows), 3),
        "rows": rows,
        "ways": {way: {"mean_s": round(statistics.mean(row["seconds"] for row in rows if row["way"] == way), 2),
                       "runs": [row["seconds"] for row in rows if row["way"] == way]} for way in WAYS},
    }


def main():
    args = parse()
    sys.path.insert(0, str(Path(args.code) / "keyit"))
    import keyit_speech as speech

    out = Path(args.out)
    scratch = Path(args.scratch) if args.scratch else out.parent / "unpack_scratch"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    report = {"rounds": args.rounds, "processors": os.cpu_count(), "engines": {}}
    try:
        for engine in args.engines:
            report["engines"][engine] = measure(speech, engine, args.archives, scratch, args.rounds)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    different = [engine for engine, result in report["engines"].items() if not result["same_files"]]
    if different or args.rounds < 1:
        sys.exit(f"not a valid comparison: {different or 'nothing was unpacked'}")


if __name__ == "__main__":
    main()
