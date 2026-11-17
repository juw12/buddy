# Talk mode bench

Run 2026-11-16 17:15 on Intel64 Family 6 Model 189 Stepping 1, GenuineIntel, 8 logical CPUs, Windows 10.

## (a) Turns: finished or not (English, real voice detector + whisper-en on Kokoro speech)

96 finished and 96 unfinished utterances in 4 lists (see the table by list); pauses (0.5, 0.8, 1.2) s; a finished turn has to be taken within 1.0 s of silence. Delays are on the audio timeline; compute time is in (b). The row with compute is measured in real time: wall clock, the speech engine reading on its own thread while the clip plays, as talk mode runs it.

| end-pointer | finished taken in time | unfinished not cut | balanced accuracy | mean delay, finished |
|---|---|---|---|---|
| Buddy (reads the words) | 98% | 88% | 92.9% | 0.563 s |
| Buddy, with compute under this load | 88% | 90% | 89.2% | 0.682 s |
| fixed silence, same delay (0.5 s) | 100% | 0% | 50.2% | 0.583 s |
| fixed silence 0.7 s | 100% | 33% | 66.5% | 0.785 s |
| fixed silence, its best gap (0.85 s) | 100% | 46% | 73.3% | 0.935 s |
| fixed silence 0.7 s, then waits for the same read (computed) | 91% | 39% | 64.9% | 0.802 s |
| fixed silence, its best gap, then waits for the same read (computed) (0.85 s) | 91% | 51% | 71.0% | 0.95 s |

| list | finished taken in time | unfinished not cut | balanced accuracy | with compute | fixed silence, same delay | fixed silence, its best gap |
|---|---|---|---|---|---|---|
| first list (its reads were seen while the rules were revised) | 100% | 92% | 95.8% | 92.4% | 50.0% | 72.9% |
| second list (written after the rules were frozen; first read in the round 2 run) | 96% | 89% | 92.4% | 90.3% | 56.2% | 72.2% |
| third list (same author, parallel in form to the second; first read in the round 3 run) | 100% | 86% | 93.1% | 86.1% | 50.0% | 75.7% |
| fourth list (same author, written after the round 4 rules; never read before this run) | 96% | 85% | 90.3% | 88.2% | 50.0% | 72.2% |

Buddy, unfinished not cut, by pause length: 0.5 s pause 98%, 0.8 s pause 86%, 1.2 s pause 79%.

Sounds finished but the speaker adds to it (7 clips, not in the accuracy): answered before the speaker went on, 0.5 s pause 43%, 0.8 s pause 100%, 1.2 s pause 100%.

Rules on typed text (no audio): en_dev 94% (n=32), en_held_out 92% (n=48), en_held_out_2 94% (n=48), en_held_out_3 94% (n=48), en_held_out_4 94% (n=48), zh 94% (n=16), id 81% (n=16).

## (a2) Turn repair: the speaker adds to something that sounded finished

7 two-part questions (whisper-en); part two starts after the pause. Whole = the question Buddy ends up with has every scripted word in order; word recall = share of the scripted words in it. Audio timeline.

| pause | answered before part two | then repaired: whole | word recall | held as one turn: whole | word recall |
|---|---|---|---|---|---|
| 0.5 s | 57% (n=4) | 50% | 94% | 67% | 97% |
| 0.8 s | 100% (n=7) | 71% | 97% | - | - |
| 1.2 s | 100% (n=7) | 71% | 97% | - | - |

The session only joins the parts if Buddy has been audible for less than 0.5 s when the speaker goes on; after that it is a new question. How soon Buddy is audible depends on the model that answers and is not measured here.

## (b) One decision

CPU load during the run: {'mean_percent': 92.0, 'max_percent': 100.0, 'logical_cpus': 8}.

| what | p50 | p95 | n |
|---|---|---|---|
| rules alone, given the words (what Jev is given) | 0.0026 ms | 0.0056 ms | 6400 |
| speech engine (whisper-en) + rules | 0.395 s | 0.662 s | 201 |
| the same in the real-time pass | 0.363 s | 5.021 s | 201 |

Read-ahead: the read starts 0.1 s into a pause and the shortest gap is 0.3 s, so the first 0.2 s of a read cost the speaker nothing. 0 of 201 reads were thrown away because the speaker had gone on.
Finished turns in real time: taken p50 0.715 s, p95 0.988 s after the last sound; that is p50 0.043 s, p95 0.347 s later than on the audio timeline (n=88).

Jev (published by the vendor, not measured by us): about 300 ms per decision, vendor cloud only, $0.042 per million input tokens.

