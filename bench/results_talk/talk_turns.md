# Talk mode bench

Run 2026-11-16 10:02 (parts a, b) and 2026-11-14 16:13 (parts c, d) on Intel64 Family 6 Model 189 Stepping 1, GenuineIntel, 8 logical CPUs, Windows 10.

## (a) Turns: finished or not (English, real voice detector + whisper-en on Kokoro speech)

48 finished and 48 unfinished utterances in two lists (see the table by list); pauses (0.5, 0.8, 1.2) s; a finished turn has to be taken within 1.0 s of silence. Delays are on the audio timeline; compute time is in (b).

| end-pointer | finished taken in time | unfinished not cut | balanced accuracy | mean delay, finished |
|---|---|---|---|---|
| Buddy (reads the words) | 98% | 78% | 87.8% | 0.589 s |
| Buddy, with compute under this load | 46% | 87% | 66.3% | 1.371 s |
| fixed silence, same delay (0.5 s) | 100% | 0% | 50.0% | 0.578 s |
| fixed silence 0.7 s | 100% | 33% | 66.7% | 0.777 s |
| fixed silence, its best gap (0.85 s) | 100% | 44% | 72.2% | 0.927 s |

| list | finished taken in time | unfinished not cut | balanced accuracy | fixed silence, same delay |
|---|---|---|---|---|
| first list (its reads were seen while the rules were revised) | 100% | 76% | 88.2% | 50.0% |
| second list (written after the rules were frozen; never read before this run) | 96% | 79% | 87.5% | 56.2% |

Buddy, unfinished not cut, by pause length: 0.5 s pause 92%, 0.8 s pause 71%, 1.2 s pause 71%.

Sounds finished but the speaker adds to it (7 clips, not in the accuracy): answered before the speaker went on, 0.5 s pause 43%, 0.8 s pause 100%, 1.2 s pause 100%.

Rules on typed text (no audio): en_dev 94% (n=32), en_held_out 88% (n=48), en_held_out_2 90% (n=48), zh 94% (n=16), id 81% (n=16).

## (b) One decision

CPU load during the run: {'mean_percent': 87.6, 'max_percent': 100.0, 'logical_cpus': 8}.

| what | p50 | p95 | n |
|---|---|---|---|
| rules alone, given the words (what Jev is given) | 0.003 ms | 0.0045 ms | 6400 |
| speech engine (whisper-en) + rules | 0.551 s | 1.343 s | 103 |

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

