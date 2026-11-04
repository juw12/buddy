"""Pointing benchmark on a real Mac: do Buddy's points land on the right thing, before and after snapping
to text read by Apple's Vision framework?

Scenes are screenshots of the website's demo desktop with exact answer boxes; the replies were recorded from
the free brain (Llama 4 Scout). Run on macOS: python mac/tests/bench_snap.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from buddy_mac import core, ocr  # noqa: E402

recorded = json.loads((HERE / "bench" / "recorded_replies.json").read_text(encoding="utf-8"))
text_by_scene = {}
for scene in sorted({item["scene"] for item in recorded}):
    text_by_scene[scene] = ocr.read_text((HERE / "bench" / f"{scene}.jpg").read_bytes())
    print(f"{scene}: {len(text_by_scene[scene])} text lines, e.g. {[t[0] for t in text_by_scene[scene][:12]]}")


def inside(point, box, pad=10):
    x0, y0, x1, y1 = box
    return point is not None and x0 - pad <= point[0] <= x1 + pad and y0 - pad <= point[1] <= y1 + pad


before = after = 0
for item in recorded:
    points = core.parse_reply(item["reply"])[1]
    snapped = core.snap_points(points, text_by_scene[item["scene"]])
    hit_before = inside(points[-1] if points else None, item["box"])
    hit_after = inside(snapped[-1] if snapped else None, item["box"])
    before += hit_before
    after += hit_after
    mark = "ok " if hit_after else "MISS"
    print(f"{mark} {item['question']:<48} {points[-1] if points else None} -> {snapped[-1] if snapped else None}")

print(f"RESULT: last point on target {before}/{len(recorded)} before snapping, {after}/{len(recorded)} after")
if not any(text_by_scene.values()):
    sys.exit("Vision returned no text at all")
sys.exit(0 if after >= before else 1)
