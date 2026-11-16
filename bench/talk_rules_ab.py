"""Two versions of the turn rules on the SAME reads: what did a change to the rules buy?

A bench run of talk_turns.py is noisy on a loaded PC (the engine's read times move from run to run), so two
runs cannot show what a change to keyit_talk.completeness() or keyit_talk.GAPS did. This script takes one
run's results - the words the engine heard for every clip, when the detector reported the pause and when
TurnTaker called the turn over - and works out what the OLD rules would have done with the same reads.
Nothing is read again and no audio is played.

    python bench/talk_rules_ab.py --results bench/results_talk/talk_turns.json --old b0e364a

`--old` is a git revision; its keyit/keyit_talk.py is the "old" rules, the working tree's is the "new" one.

How the old side is worked out:
- New rules: nothing is worked out. The fire time is the one measured in the run (`fired`), so the "new"
  scores are the run's own audio-timeline scores.
- Old rules, same kind and same gap as the new rules: the measured fire time again.
- Old rules, another gap: TurnTaker fires at the first 30 ms tick where the silence it has counted reaches
  the gap. The run tells us the tick at which that happened for the new gap, so the silence at that tick is
  known to within one tick, and the old gap is reached `(old gap - new gap) / tick` ticks later. When that
  is not a whole number of ticks there are two possible ticks; both are scored and the old result is given
  as a range (worst to best). Where the run has no fire time to start from (nothing was heard, or no turn
  within 2.5 s) the old time comes from the detector's report and its minimum silence instead ("formula").

What it can and cannot say:
- It is the audio timeline (no engine time), the same view as "buddy" in talk_turns.json, not "with compute".
- The old side is derived, not run again. The range shows how much that can matter.
- The lists are reported one by one. A list the rules were tuned on says nothing about new sentences.
"""
import argparse
import json
import math
import pathlib
import subprocess
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
TICK, BUDGET, ONSET, PAUSES = 0.03, 1.0, 0.15, (0.5, 0.8, 1.2)      # as in talk_turns.py
CLIP_TAIL = 2.5                                                      # s of silence after the speech in a clip


def rules_from(source, name):
    module = types.ModuleType(name)
    module.__file__ = name
    sys.modules[name] = module                                   # dataclasses look their module up by name
    exec(compile(source, name, "exec"), module.__dict__)
    return module


def old_rules(revision):
    shown = subprocess.run(["git", "show", f"{revision}:keyit/keyit_talk.py"], cwd=ROOT, capture_output=True)
    if shown.returncode:
        raise SystemExit(shown.stderr.decode("utf-8", "replace"))
    return rules_from(shown.stdout.decode("utf-8"), f"keyit_talk_{revision}")


def within_clip(fired):
    return round(fired, 4) if fired is not None and fired <= CLIP_TAIL else None


def old_fire(old, new, row):
    """(kind, earliest, latest, how) under the old rules. `how`: measured, exact, one tick or formula."""
    kind = old.completeness(row["heard"] or "")
    reported, measured = row["detector"], row["fired"]
    if reported is None or kind == old.EMPTY:                    # no pause seen, or nothing heard: not a turn
        return kind, None, None, "exact"
    gap = old.GAPS[kind]
    if measured is not None and row["kind"] in new.GAPS and measured > reported + 1e-9:
        ticks = (gap - new.GAPS[row["kind"]]) / TICK
        earliest = max(reported, measured + math.floor(ticks + 1e-9) * TICK)
        latest = max(reported, measured + math.ceil(ticks - 1e-9) * TICK)
        if abs(ticks) < 1e-9:
            how = "measured"
        else:
            how = "exact" if abs(latest - earliest) < 1e-9 else "one tick"
        return kind, within_clip(earliest), within_clip(latest), how
    pause_began = reported - row.get("early", 0.3)
    fires = max(reported, math.ceil((pause_began + gap) / TICK - 1e-9) * TICK)
    return kind, within_clip(fires), within_clip(fires), "formula"


def late(fired, pause):
    return fired is None or fired >= pause + ONSET


def scored(rows, fired):
    """`fired`: one fire time per row. As talk_turns.score(): finished in time, cut-off held per pause."""
    finished = [value for row, value in zip(rows, fired) if row["label"] == "finished"]
    unfinished = [value for row, value in zip(rows, fired) if row["label"] == "unfinished"]
    in_time = sum(value is not None and value <= BUDGET for value in finished)
    held = sum(late(value, pause) for value in unfinished for pause in PAUSES)
    answered = [value for value in finished if value is not None]
    out = {"finished_in_time": [in_time, len(finished)], "unfinished_held": [held, len(unfinished) * len(PAUSES)],
           "mean_delay_finished_s": round(sum(answered) / len(answered), 3) if answered else None}
    if finished and unfinished:
        out["balanced_accuracy"] = round((in_time / len(finished)
                                          + held / (len(unfinished) * len(PAUSES))) / 2, 3)
    return out


def ranged(rows, earliest, latest):
    """The old rules' scores as worst-to-best: firing early helps finished turns and hurts cut-off ones."""
    early, slow = scored(rows, earliest), scored(rows, latest)
    finished = [slow["finished_in_time"][0], early["finished_in_time"][0]]
    held = [early["unfinished_held"][0], slow["unfinished_held"][0]]
    totals = [early["finished_in_time"][1], early["unfinished_held"][1]]
    out = {"finished_in_time": [finished, totals[0]], "unfinished_held": [held, totals[1]],
           "mean_delay_finished_s": [early["mean_delay_finished_s"], slow["mean_delay_finished_s"]]}
    if all(totals):
        out["balanced_accuracy"] = [round((finished[side] / totals[0] + held[side] / totals[1]) / 2, 3)
                                    for side in (0, 1)]
    return out


def outcome(row, fired):
    if row["label"] == "finished":
        return "in time" if fired is not None and fired <= BUDGET else "late"
    return "held %d of %d pauses" % (sum(late(fired, pause) for pause in PAUSES), len(PAUSES))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--results", required=True, help="talk_turns.json of one bench run")
    parser.add_argument("--old", required=True, help="git revision whose rules are the 'old' ones")
    parser.add_argument("--out", help="write the comparison here as JSON")
    arguments = parser.parse_args()

    results = json.loads(pathlib.Path(arguments.results).read_text(encoding="utf-8"))
    rows = [row for row in results["turn_rows"] if row["label"] in ("finished", "unfinished")]
    new = rules_from((ROOT / "keyit" / "keyit_talk.py").read_text(encoding="utf-8"), "keyit_talk_new")
    old = old_rules(arguments.old)
    before = [old_fire(old, new, row) for row in rows]
    after = [row["fired"] for row in rows]
    how = {name: sum(1 for item in before if item[3] == name)
           for name in ("measured", "exact", "one tick", "formula")}
    same_kind = sum(1 for row in rows if new.completeness(row["heard"] or "") == row["kind"])

    changed = []
    for row, (kind_old, earliest, latest, method) in zip(rows, before):
        was = sorted({outcome(row, earliest), outcome(row, latest)})
        now = outcome(row, row["fired"])
        if was != [now]:
            changed.append({"list": row.get("list", "first"), "label": row["label"], "heard": row["heard"],
                            "old": [kind_old, earliest, latest, " or ".join(was)],
                            "new": [row["kind"], row["fired"], now],
                            "sure": len(was) == 1, "old_time_is": method})

    lists = {}
    for name in sorted({row.get("list", "first") for row in rows}) + ["all"]:
        chosen = [index for index, row in enumerate(rows) if name == "all" or row.get("list", "first") == name]
        picked = [rows[index] for index in chosen]
        lists[name] = {"old": ranged(picked, [before[index][1] for index in chosen],
                                     [before[index][2] for index in chosen]),
                       "new": scored(picked, [after[index] for index in chosen])}

    report = {"results": str(arguments.results), "old_revision": arguments.old, "clips": len(rows),
              "new_is": "the fire times measured in the run (audio timeline)",
              "old_is": "derived from the run's fire times; ranges are worst to best where a time is "
                        "known to one 30 ms tick only",
              "old_time_from": how, "new_rules_give_the_run_s_kind": [same_kind, len(rows)],
              "old_gaps": old.GAPS, "new_gaps": new.GAPS, "lists": lists, "changed": changed}

    print(f"{len(rows)} clips; old fire time: {how}; the new rules give the run's kind for "
          f"{same_kind}/{len(rows)}")
    for name, both in lists.items():
        was, now = both["old"], both["new"]
        print(f"  {name:7s} old  finished in time {was['finished_in_time'][0]}/{was['finished_in_time'][1]}"
              f"  cut-off held {was['unfinished_held'][0]}/{was['unfinished_held'][1]}"
              f"  balanced {was.get('balanced_accuracy')}")
        print(f"  {name:7s} new  finished in time {now['finished_in_time'][0]}/{now['finished_in_time'][1]}"
              f"  cut-off held {now['unfinished_held'][0]}/{now['unfinished_held'][1]}"
              f"  balanced {now.get('balanced_accuracy')}  mean delay {now['mean_delay_finished_s']} s")
    print(f"{len(changed)} clips with another outcome ({sum(item['sure'] for item in changed)} of them "
          f"whichever tick the old rules fired at):")
    for item in changed:
        print(f"  [{item['list']}] {item['label']:10s} {item['old'][0]} {item['old'][1]}..{item['old'][2]} "
              f"({item['old'][3]}) -> {item['new'][0]} {item['new'][1]} ({item['new'][2]})  {item['heard']!r}")
    if arguments.out:
        pathlib.Path(arguments.out).write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n",
                                               encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
