# Talk mode bench

Run 2026-11-16 16:40 (parts a, a2, b) and 2026-11-16 13:13 (parts c, d) on Intel64 Family 6 Model 189 Stepping 1, GenuineIntel, 8 logical CPUs, Windows 10.

## (a) Turns: finished or not (English, real voice detector + whisper-en on Kokoro speech)

96 finished and 96 unfinished utterances in 4 lists (see the table by list); pauses (0.5, 0.8, 1.2) s; a finished turn has to be taken within 1.0 s of silence. Delays are on the audio timeline; compute time is in (b). The row with compute is measured in real time: wall clock, the speech engine reading on its own thread while the clip plays, as talk mode runs it.

| end-pointer | finished taken in time | unfinished not cut | balanced accuracy | mean delay, finished |
|---|---|---|---|---|
| Buddy (reads the words) | 98% | 88% | 92.9% | 0.563 s |
| Buddy, with compute under this load | 90% | 90% | 89.8% | 0.642 s |
| fixed silence, same delay (0.5 s) | 100% | 0% | 50.2% | 0.583 s |
| fixed silence 0.7 s | 100% | 33% | 66.5% | 0.785 s |
| fixed silence, its best gap (0.85 s) | 100% | 46% | 73.3% | 0.935 s |
| fixed silence 0.7 s, then waits for the same read (computed) | 90% | 40% | 64.6% | 0.789 s |
| fixed silence, its best gap, then waits for the same read (computed) (0.85 s) | 90% | 52% | 70.7% | 0.938 s |

| list | finished taken in time | unfinished not cut | balanced accuracy | with compute | fixed silence, same delay | fixed silence, its best gap |
|---|---|---|---|---|---|---|
| first list (its reads were seen while the rules were revised) | 100% | 92% | 95.8% | 89.6% | 50.0% | 72.9% |
| second list (written after the rules were frozen; first read in the round 2 run) | 96% | 89% | 92.4% | 91.0% | 56.2% | 72.2% |
| third list (same author, parallel in form to the second; first read in the round 3 run) | 100% | 86% | 93.1% | 91.0% | 50.0% | 75.7% |
| fourth list (same author, written after the round 4 rules; never read before this run) | 96% | 85% | 90.3% | 87.5% | 50.0% | 72.2% |

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

CPU load during the run: {'mean_percent': 90.8, 'max_percent': 100.0, 'logical_cpus': 8}.

| what | p50 | p95 | n |
|---|---|---|---|
| rules alone, given the words (what Jev is given) | 0.0029 ms | 0.0047 ms | 6400 |
| speech engine (whisper-en) + rules | 0.386 s | 1.361 s | 201 |
| the same in the real-time pass | 0.354 s | 4.1 s | 201 |

Read-ahead: the read starts 0.1 s into a pause and the shortest gap is 0.3 s, so the first 0.2 s of a read cost the speaker nothing. 0 of 201 reads were thrown away because the speaker had gone on.
Finished turns in real time: taken p50 0.639 s, p95 0.834 s after the last sound; that is p50 0.043 s, p95 0.281 s later than on the audio timeline (n=87).

Jev (published by the vendor, not measured by us): about 300 ms per decision, vendor cloud only, $0.042 per million input tokens.

## (c) Barge-in (SIMULATED loudspeaker-to-microphone path)

| voice | echo at the mic / playback | Buddy speech | false barge-ins |
|---|---|---|---|
| natural | 0.02 | 3.0 min | 0 |
| natural | 0.2 | 3.0 min | 0 |
| natural | 0.8 | 3.0 min | 0 |
| natural | 2.0 | 3.0 min | 0 |
| blind (SAPI) | 0.02 | 3.0 min | 0 |
| blind (SAPI) | 0.2 | 3.0 min | 0 |
| blind (SAPI) | 0.8 | 3.0 min | 0 |
| blind (SAPI) | 2.0 | 3.0 min | 0 |

| voice | user vs echo at the mic | noticed within 0.5 s | within 1 s | onset to stop, p50 | p95 |
|---|---|---|---|---|---|
| natural | headset | 100% | 100% | 0.11 | 0.14 |
| natural | x1.5 | 29% | 29% | 0.19 | 0.2 |
| natural | x2 | 46% | 46% | 0.19 | 0.2 |
| natural | x3 | 100% | 100% | 0.2 | 0.2 |
| natural | x4 | 100% | 100% | 0.2 | 0.2 |
| natural | x6 | 100% | 100% | 0.17 | 0.2 |
| natural | x10 | 100% | 100% | 0.14 | 0.17 |
| blind (SAPI) | headset | 100% | 100% | 0.14 | 0.14 |
| blind (SAPI) | x1.5 | 0% | 0% | - | - |
| blind (SAPI) | x2 | 0% | 0% | - | - |
| blind (SAPI) | x3 | 54% | 54% | 0.19 | 0.2 |
| blind (SAPI) | x4 | 100% | 100% | 0.2 | 0.2 |
| blind (SAPI) | x6 | 100% | 100% | 0.2 | 0.2 |
| blind (SAPI) | x10 | 100% | 100% | 0.17 | 0.2 |

EchoGate.hear compute per 30 ms tick: p95 0.1191 ms.

## (d) Voice on the real output device (silent samples)

Device: Headphones (Realtek(R) Audio) (44100 Hz); 8 rounds; the samples played were silence, so nothing was audible.

| what | p50 | p95 | max | mean | n |
|---|---|---|---|---|---|
| pause requested to silent | 0.05 s | 0.05 s | 0.05 s | 0.05 s | 8 |
| resume requested to samples flowing | 0.145 s | 0.371 s | 0.371 s | 0.144 s | 8 |
| stop requested to device idle | 0.201 s | 3.317 s | 3.317 s | 0.586 s | 8 |

