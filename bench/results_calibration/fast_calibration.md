# System 1, trained and calibrated by simulation: held-out results

Model `keyit/minds_model.json`, sha256 `a25000d89af5f45a`. Scored 2026-11-17 15:16.
"1.5.0" is the same code with no model file: the hand counts of Buddy 1.5.0.

| set | requests | one-step commands | done fast, right (1.5.0 -> trained) | fast actions (1.5.0 -> trained) | wrong fast (1.5.0 -> trained) | 95% upper bound on wrong share of trained fast actions | readings right | ECE |
|---|---|---|---|---|---|---|---|---|
| users: simulated users written by Mistral Small 3.1 (four people) | 364 | 191 | 23 -> 42 | 24 -> 43 | 0 -> 0 | 6.7% | 45/46 | 0.0356 |
| vendor: Windows Voice Access command list, app and window commands | 44 | 38 | 29 -> 31 | 29 -> 31 | 0 -> 0 | 9.2% | 32/32 | 0.025 |
| old test: the TEST list of Buddy 1.5.0 | 94 | 49 | 43 -> 40 | 43 -> 40 | 0 -> 0 | 7.2% | 47/47 | 0.0745 |
| CLINC150: requests for other assistants, test + oos_test | 5500 | 0 | 0 -> 0 | 0 -> 0 | 0 -> 0 | n/a | 0/0 | None |
| speech (af_heart): simulated requests said by Kokoro, heard by whisper-en | 100 | 75 | 14 -> 43 | 14 -> 43 | 0 -> 0 | 6.7% | 49/53 | 0.0511 |
| speech (am_michael): simulated requests said by Kokoro, heard by whisper-en | 100 | 75 | 14 -> 45 | 14 -> 45 | 0 -> 0 | 6.4% | 51/55 | 0.0497 |
| grammar (test seed): bench/simulate.py with a seed that was not trained on: in-simulator | 20000 | 12957 | 2783 -> 9302 | 2966 -> 9392 | 183 -> 90 | 1.1% | 9956/10511 | 0.0266 |

Together, the sets whose wording the simulator did not write (users, vendor, old test, CLINC150): 114 fast actions, 0 wrong; with 95% confidence the wrong share is under 2.6%.
With the two speech sets added: 202 fast actions, 0 wrong, under 1.5%. The speech sets are the same 100 grammar-simulator requests said by two voices, so they are neither independent of each other nor free of the simulator's wording.

The last rows named "grammar" are in-simulator: the program that trained the mind wrote them too.

## Reliability on these sets together (the grammar rows are not in it)

233 readings, 224 right, ECE 0.0279.

| stated confidence | readings | stated (mean) | right | overconfident by |
|---|---|---|---|---|
| 0.5-0.6 | 12 | 0.5459 | 0.5 | +0.0459 |
| 0.6-0.7 | 7 | 0.687 | 0.5714 | +0.1156 |
| 0.7-0.8 | 2 | 0.7175 | 1.0 | -0.2825 |
| 0.8-0.9 | 9 | 0.8759 | 1.0 | -0.1241 |
| 0.9-1.0 | 203 | 0.9829 | 1.0 | -0.0171 |

## Stakes: of the readings that passed an action's bar, how many were right

| action | bar | passed | right | share | meets the bar |
|---|---|---|---|---|---|
| focus | 0.8 | 26 | 26 | 1.0 | yes |
| minimize | 0.8 | 42 | 42 | 1.0 | yes |
| maximize | 0.8 | 37 | 37 | 1.0 | yes |
| open | 0.85 | 58 | 58 | 1.0 | yes |
| close | 0.95 | 39 | 39 | 1.0 | yes |

Decision time over 6202 held-out requests: median 0.0162 ms, p95 0.0392 ms, max 8.8711 ms.

## Gates

- PASS: G2 no more wrong fast than 1.5.0 on any set
- PASS: G2 old test list still 0 wrong fast
- PASS: G3 ECE <= 0.03
- PASS: G3 no big bin overconfident by more than 0.05
- PASS: G4 every action as right as its bar
- PASS: G5 more commands done fast than 1.5.0
- PASS: G6 a decision under 1 ms at p95

## Reliability in the simulator (grammar, test seed)

ECE 0.0266. The two simulators disagree about some requests ("open settings", "launch X" when X is already open): the grammar assumes the person often means something else, the language-model users nearly always mean the app. The trained confidence sits between the two, under every bar, so System 1 leaves these to System 2; against the grammar alone those bins read as overconfident.

| stated confidence | readings | stated (mean) | right | overconfident by |
|---|---|---|---|---|
| 0.0-0.1 | 44 | 0.0776 | 0.3409 | -0.2633 |
| 0.1-0.2 | 31 | 0.1309 | 0.1613 | -0.0304 |
| 0.5-0.6 | 191 | 0.5459 | 0.4712 | +0.0747 |
| 0.6-0.7 | 101 | 0.6864 | 0.5149 | +0.1715 |
| 0.7-0.8 | 248 | 0.75 | 0.3226 | +0.4274 |
| 0.8-0.9 | 444 | 0.8672 | 0.7748 | +0.0924 |
| 0.9-1.0 | 9452 | 0.982 | 0.9913 | -0.0093 |

## Wrong fast actions of the trained mind, every one

- grammar (test seed): "go to settings" -> ['focus', 'Settings'] at 0.8845; wanted a look first (app settings)
- grammar (test seed): "close edge please" -> ['close', 'Microsoft Edge'] at 0.9554; wanted a look first (close file)
- grammar (test seed): "could you go to settings" -> ['focus', 'Settings'] at 0.8845; wanted a look first (inside an app)
- grammar (test seed): "would you go to settings?" -> ['focus', 'Settings'] at 0.8845; wanted a look first (inside an app)
- grammar (test seed): "just open goolge.com" -> ['open', 'goolge.com'] at 0.9641; wanted ['open', 'google.com'] (open site)
- grammar (test seed): "go to settings" -> ['focus', 'Settings'] at 0.8845; wanted a look first (app settings)
- grammar (test seed): "go to settings" -> ['focus', 'Settings'] at 0.8845; wanted a look first (inside an app)
- grammar (test seed): "go to settings" -> ['focus', 'Settings'] at 0.8845; wanted a look first (app settings)
- grammar (test seed): "can you launch ogogle.com?" -> ['open', 'ogogle.com'] at 0.9803; wanted ['open', 'google.com'] (open site)
- grammar (test seed): "Please close microsoft teams please." -> ['close', 'Microsoft Teams'] at 0.9554; wanted a look first (close file)
- grammar (test seed): "can you open aazon.com?" -> ['open', 'aazon.com'] at 0.9641; wanted ['open', 'amazon.com'] (open site)
- grammar (test seed): "Open pthon.org" -> ['open', 'pthon.org'] at 0.9641; wanted ['open', 'python.org'] (open site)
- grammar (test seed): ... and 78 more (in the JSON)

## Habits in simulated use

Simulated people say a dozen phrases of their own ("outlook front", "bye notepad") again and again. The brain does a phrase until its habit is sure enough for System 1.

| brain wrong | means something else | says "no" when wrong | requests | fast by habit | wrong fast | per 1,000 requests | habit confidence stated | habit right | ECE |
|---|---|---|---|---|---|---|---|---|---|
| 0.0% | 0.0% | 100.0% | 50000 | 37402 | 0 | 0.0 | 0.9051 | 1.0 | 0.0949 |
| 5.0% | 2.0% | 100.0% | 50000 | 33757 | 662 | 13.24 | 0.862 | 0.9563 | 0.0998 |
| 5.0% | 2.0% | 50.0% | 50000 | 33324 | 662 | 13.24 | 0.8509 | 0.9337 | 0.0948 |
| 10.0% | 10.0% | 50.0% | 50000 | 23485 | 2295 | 45.9 | 0.7301 | 0.7706 | 0.0928 |
| 5.0% | 2.0% | 0.0% | 50000 | 33266 | 665 | 13.3 | 0.8438 | 0.9136 | 0.0887 |
