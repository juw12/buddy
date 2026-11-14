# Talk mode bench

Run 2026-11-14 16:41 (parts a, b) and 2026-11-14 16:13 (parts c, d) on Intel64 Family 6 Model 189 Stepping 1, GenuineIntel, 8 logical CPUs, Windows 10.

## (a) Turns: finished or not (English, real voice detector + whisper-en on Kokoro speech)

24 finished and 24 unfinished held-out utterances; pauses (0.5, 0.8, 1.2) s; a finished turn has to be taken within 1.0 s of silence. Delays are on the audio timeline; compute time is in (b).

| end-pointer | finished taken in time | unfinished not cut | balanced accuracy | mean delay, finished |
|---|---|---|---|---|
| Buddy (reads the words) | 100% | 71% | 85.4% | 0.549 s |
| Buddy, with compute under this load | 71% | 74% | 72.2% | 0.983 s |
| fixed silence, same delay (0.45 s) | 100% | 0% | 50.0% | 0.531 s |
| fixed silence 0.7 s | 100% | 33% | 66.7% | 0.779 s |
| fixed silence, its best gap (0.9 s) | 83% | 62% | 72.9% | 0.981 s |

Buddy, unfinished not cut, by pause length: 0.5 s pause 96%, 0.8 s pause 58%, 1.2 s pause 58%.

Sounds finished but the speaker adds to it (7 clips, not in the accuracy): answered before the speaker went on, 0.5 s pause 43%, 0.8 s pause 100%, 1.2 s pause 100%.

Rules on typed text (no audio): en_dev 84% (n=32), en_held_out 79% (n=48), zh 94% (n=16), id 81% (n=16).

## (b) One decision

CPU load during the run: {'mean_percent': 89.8, 'max_percent': 100.0, 'logical_cpus': 8}.

| what | p50 | p95 | n |
|---|---|---|---|
| rules alone, given the words (what Jev is given) | 0.0027 ms | 0.0048 ms | 4000 |
| speech engine (whisper-en) + rules | 0.46 s | 0.874 s | 55 |

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

EchoGate.hear compute per 30 ms tick: p95 0.1195 ms.

## (d) Voice on the real output device (silent samples)

Not measured: the output device did not start.

