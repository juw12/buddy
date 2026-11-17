# Thinking fast and slow: System 1 on two lists

CPU load while measuring: 89.3%. Lists are English text by the rules' author.

| | DEV (rules counted on it) | TEST (held out) |
|---|---|---|
| Requests | 96 | 94 |
| One-step commands | 53 | 49 |
| Must think first | 43 | 45 |
| Done by System 1 | 48 | 43 |
| Wrong fast actions | 0 | 0 |
| One-step commands done fast | 48 | 43 |
| Calibration error (ECE) | 0.0668 | 0.0612 |

| Rule | Stated confidence | Right on TEST | Stated minus observed |
|---|---|---|---|
| app | 0.938 | 14 of 14 | -0.062 |
| site | 0.9 | 4 of 4 | -0.1 |
| this | 0.875 | 4 of 4 | -0.125 |
| window | 0.958 | 22 of 22 | -0.042 |

Left to the brain although one step: "close this app", "close the current window", "maximize this window please", "could you minimize excel", "copy this", "type best regards"

One decision: 0.0106 ms median, 0.0233 ms p95, 2.0936 ms slowest, over 38000 decisions.

| Action | Bar | Times the brain must do it first |
|---|---|---|
| focus | 0.8 | 3 |
| minimize | 0.8 | 3 |
| maximize | 0.8 | 3 |
| open | 0.85 | 5 |
| keys | 0.9 | 8 |
| close | 0.95 | 18 |

Real actions on the bench's own window: 0.0141 s median, 0.092 s slowest, all done and checked: True.

The same kind of command through the free cloud brain (https://buddy-brain.juwenmondial.workers.dev/v1/chat/completions): 3.3535 s median (2.932 to 4.19 s), 0 errors in 12 asks.
