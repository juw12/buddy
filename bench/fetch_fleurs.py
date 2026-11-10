"""Fetch a few real human recordings per language from FLEURS (CC-BY 4.0, Google) without the full download.

The official per-language archive (storage.googleapis.com/xtreme_translations/FLEURS102/<lang>.tar.gz, 1-3 GB)
starts with dev.tsv followed by the dev recordings, so reading the first part of the stream is enough.
Usage: python fetch_fleurs.py <out_dir> <lang>:<count> [<lang>:<count> ...]
"""
import json
import os
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

SOURCE = "https://storage.googleapis.com/xtreme_translations/FLEURS102/{language}.tar.gz"


def opener():
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    return urllib.request.build_opener(*handlers)


def fetch(language, count, out_dir):
    folder = Path(out_dir) / language
    manifest_path = folder / "manifest.json"
    if manifest_path.exists() and len(json.loads(manifest_path.read_text(encoding="utf-8"))) >= count:
        print(language, "already fetched", flush=True)
        return
    folder.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        SOURCE.format(language=language),
        headers={"User-Agent": "Mozilla/5.0 Buddy-bench"},
    )
    transcripts = {}
    clips = []
    sentences_seen = set()
    with opener().open(request, timeout=60) as response:
        with tarfile.open(fileobj=response, mode="r|gz") as archive:
            for member in archive:
                name = member.name.rsplit("/", 1)[-1]
                if name == "dev.tsv":
                    text = archive.extractfile(member).read().decode("utf-8")
                    for line in text.splitlines():
                        cells = line.split("\t")
                        # id, file name, raw transcription, normalised transcription, phonemes, samples, gender
                        if len(cells) >= 3:
                            transcripts[cells[1]] = {
                                "sentence_id": cells[0],
                                "text": cells[2],
                                "gender": cells[-1],
                            }
                    continue
                if not name.endswith(".wav") or "/dev/" not in member.name:
                    continue
                entry = transcripts.get(name)
                if entry is None:
                    continue
                # One recording per sentence, so the set covers more text and more voices.
                if entry["sentence_id"] in sentences_seen:
                    continue
                sentences_seen.add(entry["sentence_id"])
                (folder / name).write_bytes(archive.extractfile(member).read())
                clips.append({"file": name, **entry})
                if len(clips) >= count:
                    break
    manifest_path.write_bytes(json.dumps(clips, ensure_ascii=False, indent=1).encode("utf-8"))
    print(language, len(clips), "clips", flush=True)


if __name__ == "__main__":
    target = sys.argv[1]
    for spec in sys.argv[2:]:
        language, _, count = spec.partition(":")
        started = time.time()
        try:
            fetch(language, int(count or 10), target)
        except Exception as error:
            print(language, "FAILED", repr(error), flush=True)
        print(f"  {time.time() - started:.0f}s", flush=True)
    print("done", flush=True)
