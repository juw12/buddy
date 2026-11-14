# Voice benchmark

How Buddy picks its speech engine: every engine hears the same recordings and is scored the same way.
The numbers in the main README ("Voice: Buddy vs Wispr Flow vs Handy") come from here.

* `corpus.json`: 12 English work questions (with jargon, fillers and a spoken correction) and 4 Chinese ones.
* Audio (`set_clean/`, `set_noisy/`, `set_silence/`, 16 kHz WAV, ~13 MB) is attached to the GitHub release as
  `buddy-voice-bench-audio.zip`, not kept in git. Clean = Windows voices David/Zira (English) and Huihui/Hanhan (Chinese);
  noisy = the same with pink noise at 10 dB SNR; silence = room tone, fan hum, key clicks and a breath (must give no text).
* `bench_lib.py`: loading and scoring. Word error rate for English (digits and number words count as the same,
  "um"/"uh" are ignored), character error rate for Chinese, jargon hits, made-up text on the silence clips.
  Cleaned-up output (Buddy's *My words* + corrections) is scored against what the speaker meant (`INTENDED`).
* `run_windows.py`: the Windows recognizer (what Buddy fell back to when the Whisper download was blocked).
* `run_sherpa.py <engine> [--vad]`: one sherpa-onnx engine, with or without Silero voice detection.
  Models: unpack the sherpa-onnx `asr-models` release archives into `bench/models/` (or set `BUDDY_BENCH_MODELS`).
* `run_shipped.py`: Buddy's real code path (`keyit_speech.Speech("auto")` + `keyit_words`), as users get it.
  Set `KEYIT_HOME` to a folder whose `speech/` holds the models.
* `coexist.py ort-first|sherpa-first`: the Kokoro voices (onnxruntime) and speech in (sherpa-onnx) load in one process.
* `results/*.json`: every transcript and the summary of each run.

Latency is measured on the PC running the test; it doubles when other heavy work shares the CPU, so compare
engines from the same run.

## Round 2: real speech in twelve languages

Round 1 used computer voices. Round 2 uses people: 150 dev recordings from Google's FLEURS set (CC BY 4.0), each one
a person reading a sentence aloud on their own device. English, Indonesian and Mandarin have 20 clips each; Malay,
Japanese, Korean, Thai, Vietnamese, German, Spanish, French and Cantonese have 10 each.

* `fetch_fleurs.py <out_dir> <lang>:<count> ...`: reads the first clips of each language from the official FLEURS
  archives into `real/<FLEURS code>/` with a `manifest.json`. The audio is not kept in git.
* `real_lib.py`: which clips, how text is compared, added noise.
* `run_real.py --out <file>`: Buddy's real code path (`keyit_speech.Speech`) with the language set as in Settings.
  `--code <checkout>` measures another checkout, so 1.2.0 and a branch hear the same clips. `--raw <engine>` calls
  one sherpa-onnx engine directly, and `--detect` lets it name the language. The silence clips of round 1 run too
  (`--no-silence` skips them). The script exits with an error when a language fails or nothing was measured.
* `my_words_safety.py`: runs My words over every transcript and counts each term it writes that nobody said.
* `latency_load.py`: decodes the same clips at normal and at raised priority, with the PC as it is and
  with every core busy, and exits non-zero when the raise changes nothing.
* `tables_r2.py`: prints every table below from `results_r2/*.json`. No number in them is typed by hand.
* `results_r2/*.json`: every transcript and the summary of each run. Folder paths of the test PC were replaced.

How the text is scored:

* Both sides are normalised before they are compared: Unicode NFKC, lower case, punctuation dropped.
* Chinese, Cantonese, Japanese, Korean and Thai are scored by character (CER), the other languages by word (WER).
* Chinese is folded to Simplified characters on both sides. English digits 0 to 12 count the same as the words.
* A number written in another format than the reference still counts as an error, so use the figures to compare
  engines on the same clips.
* Decode time depends on what else the PC was doing. Compare times only inside one run.

### Buddy 1.2.0 against this branch, end to end

The same clips through `keyit_speech.Speech`, with the language chosen in Settings.

| Language | Clips | 1.2.0 engine | 1.2.0 error | Engine now | Error now | Decode now (mean) | Clip length (mean) |
|---|---|---|---|---|---|---|---|
| English | 20 | Whisper base.en | 12.7% WER | Whisper base.en | 12.7% WER | 7.06 s | 8.4 s |
| Chinese (Mandarin) | 20 | SenseVoice | 10.3% CER | SenseVoice | 10.3% CER | 2.20 s | 11.5 s |
| Cantonese | 10 | SenseVoice | 6.3% CER | SenseVoice | 6.3% CER | 4.39 s | 11.9 s |
| Indonesian | 20 | Parakeet V3 | 95.7% WER | Omnilingual | 29.1% WER | 11.73 s | 12.4 s |
| Malay | 10 | Parakeet V3 | 94.2% WER | Dolphin | 28.1% WER | 1.56 s | 10.3 s |
| Japanese | 10 | SenseVoice | 80.7% CER | Dolphin | 22.1% CER | 1.55 s | 12.5 s |
| Korean | 10 | SenseVoice | 75.6% CER | Dolphin | 13.8% CER | 1.45 s | 11.0 s |
| Thai | 10 | Parakeet V3 | 99.8% CER | Dolphin | 19.2% CER | 1.91 s | 13.4 s |
| Vietnamese | 10 | Parakeet V3 | 99.7% WER | Dolphin | 26.2% WER | 2.27 s | 14.7 s |
| German | 10 | Parakeet V3 | 18.5% WER | Parakeet V3 | 18.5% WER | 10.33 s | 18.4 s |
| Spanish | 10 | Parakeet V3 | 1.4% WER | Parakeet V3 | 1.4% WER | 5.67 s | 10.0 s |
| French | 10 | Parakeet V3 | 8.2% WER | Parakeet V3 | 8.2% WER | 5.29 s | 10.1 s |

The 1.2.0 runs were taken while other heavy work saturated the CPU, and this branch's runs on a calmer one, so only
the error rates are compared; no 1.2.0 decode time is shown. For English, Indonesian, Chinese (Mandarin), Cantonese,
Malay, Japanese, Korean, Thai and Vietnamese the 1.2.0 transcripts were not saved: `v0_real_a_summary.json` holds the
summary line of each language only. German, Spanish and French have every transcript in `v0_real_b.json`.

### Silence clips

Room tone, fan hum, key clicks and a breath. An engine passes when it types nothing.

* `v0_real_b.json`: 4 silence clips x 1 engine(s): 0 typed something
* `shipped_r2_a.json`: 4 silence clips x 3 engine(s): 0 typed something
* `shipped_r2_b.json`: 4 silence clips x 1 engine(s): 0 typed something
* `shipped_r2_c.json`: 4 silence clips x 1 engine(s): 0 typed something
* `shipped_r2_auto.json`: 4 silence clips x 1 engine(s): 0 typed something

### Auto-detect, end to end

Auto-detect lets Whisper base name the language and write the text. The last error column is the same
clips with the language chosen in Settings.

| Language | Clips | Engine | Error on Auto-detect | Error with the language chosen, same clips | Decode (mean) |
|---|---|---|---|---|---|
| English | 10 | Whisper base | 15.7% WER | 13.0% WER | 5.79 s |
| Indonesian | 10 | Whisper base | 54.8% WER | 29.8% WER | 9.44 s |
| Chinese (Mandarin) | 10 | Whisper base | 19.1% CER | 10.3% CER | 7.67 s |

### Engines called directly

One engine at a time on 10 clips per language, without Buddy's routing. The CPU was saturated by other work
during these runs, so compare their decode times only with each other. Whisper small was not measured: its
model archive on the test PC was damaged.

| Engine | Language | Clips | Error | Decode (mean) |
|---|---|---|---|---|
| omnilingual | Indonesian | 10 | 29.8% WER | 19.24 s |
| omnilingual | Malay | 10 | 33.3% WER | 8.26 s |
| omnilingual | Thai | 10 | 18.5% CER | 12.56 s |
| omnilingual | Vietnamese | 10 | 32.9% WER | 7.02 s |
| dolphin-base | Indonesian | 10 | 43.8% WER | 11.50 s |
| dolphin-base | Malay | 10 | 28.1% WER | 8.76 s |
| dolphin-base | Thai | 10 | 19.2% CER | 10.44 s |
| dolphin-base | Vietnamese | 10 | 26.2% WER | 11.80 s |
| dolphin-base | Japanese | 10 | 22.1% CER | 5.77 s |
| dolphin-base | Korean | 10 | 13.8% CER | 6.41 s |
| dolphin-small | Indonesian | 10 | 40.9% WER | 9.86 s |
| dolphin-small | Malay | 10 | 22.8% WER | 4.99 s |
| dolphin-small | Thai | 10 | 12.7% CER | 11.56 s |
| dolphin-small | Vietnamese | 10 | 31.9% WER | 17.22 s |
| sensevoice | Japanese | 10 | 80.9% CER | 2.27 s |
| sensevoice | Korean | 10 | 76.3% CER | 2.54 s |
| sensevoice | Chinese (Mandarin) | 10 | 9.8% CER | 11.15 s |
| sensevoice | Cantonese | 10 | 6.0% CER | 7.64 s |
| sensevoice | English | 10 | 30.8% WER | 16.39 s |
| whisper-base | Indonesian | 10 | 54.8% WER | 17.30 s |
| whisper-base | Malay | 10 | 58.5% WER | 16.62 s |
| whisper-base | English | 10 | 15.7% WER | 8.48 s |
| whisper-base | Chinese (Mandarin) | 10 | 19.1% CER | 22.35 s |
| whisper-base (detects the language) | Indonesian | 10 | 54.8% WER | 6.66 s |
| whisper-base (detects the language) | English | 10 | 15.7% WER | 4.46 s |
| whisper-base (detects the language) | Chinese (Mandarin) | 10 | 19.1% CER | 8.24 s |

Routing on this branch: Indonesian uses omnilingual; Malay, Japanese, Korean, Thai and Vietnamese use
dolphin-base; Mandarin and Cantonese stay on SenseVoice.

### Language detection

Whisper base named the right language on 30 of 30 clips.

### Cantonese script

The FLEURS Cantonese references are written in Traditional characters. The conversion works character by
character.

| Cantonese against the Traditional reference | CER | Wrong characters |
|---|---|---|
| as the model wrote it (Simplified) | 33.3% | 117 of 351 |
| after Buddy's `to_traditional()` | 6.0% | 21 of 351 |
| both sides folded to one script | 6.0% | 21 of 351 |

### My words safety

Bench list: SRM, PLM, BOM, eGreen, PureGMS, ZIRIS, Pega-Vision, Spec Green, Vendor Mapping, Notepad, Outlook.
A replacement is wrong when the term comes out more often than it was said and more often than the engine
had already written it.

|  | Buddy 1.2.0 | Letters rule, tightened | This branch |
|---|---|---|---|
| Texts / runs | 684 / 1374 | 668 / 2684 | 721 / 2896 |
| Runs My words changed | 162 | 226 | 218 |
| Wrong replacements | 56 | 0 | 0 |
| of those: a sentence that already had the term was changed | 6 | 0 | 0 |
| "Bizaira's power test" becomes ZIRIS | no | yes | yes |
| "Zaira's answer was wrong." comes out as | Zaira's answer was wrong. | ZIRIS answer was wrong. | Zaira's answer was wrong. |
| Jargon kept, Buddy's round-1 transcripts, clean | 16 of 22 | 18 of 22 | 18 of 22 |
| Jargon kept, Buddy's round-1 transcripts, noisy | 16 of 22 | 17 of 22 | 17 of 22 |
| Jargon kept, all round-1 result files | 186 of 308 | 204 of 308 | 202 of 308 |
| Dictionary sweep: single words rewritten | 9 of 9 | 0 of 9 | 0 of 9 |
| Dictionary sweep: word pairs rewritten | 5396 of 5396 | 456 of 5396 | 56 of 5396 |

* The middle column was run on 668 texts, when the hand-written near misses numbered 25. They number 41 now.
* Dictionary sweep: every English dictionary word, and every pair of words, that passes the letters test of
  the tree under test is put into "We saw ... there." and run through that tree's own `process()`.
  Three-word chunks, names and words of other languages are not covered.
* Of the 56 pairs this branch still rewrites, 54 are Notepad or Outlook next to one letter
  ("notepad a" becomes "Notepad a": only the capital changes) and 2 are true homophones
  ("speck green", "vender mapping").
* People's names are never rewritten: 37,809 first and last names of 63 Faker locales (`keyit/names/`,
  MIT) count as real words. Before that list the sound rule turned "Zaira's answer" into "ZIRIS answer".
  On the same 721 texts the version without the list (`my_words_safety_before_names.json`) gives the same
  numbers in every row except that one; jargon kept and the sweep do not change. A name missing from the
  list is still replaced if it sounds exactly like a term; adding it to My words keeps it.
* This branch was run on 721 texts: the timing result files added since the first run hold a few more
  transcripts.
* Across all round-1 result files this branch keeps 2 fewer jargon terms than the tightened letters rule,
  because real words are now replaced only when they sound exactly like the term.

### Release to text on a busy PC

| The PC | Clips | Decoded as 1.2.0 did, mean (median) | Decoded as shipped, mean (median) | Shipped faster on | Same text on |
|---|---|---|---|---|---|
| As it was | 6 | 4.80 s (4.44 s) | 1.02 s (1.00 s) | 6 of 6 | 6 of 6 |
| Every core busy | 6 | 3.00 s (3.19 s) | 1.04 s (1.02 s) | 6 of 6 | 6 of 6 |

Whisper base.en on English clips, 4 decode threads on 8 logical processors, 8 busy loops for the loaded row.

* `latency_load.py` decodes every clip twice in each row through `keyit_speech.Speech`: once at normal
  priority, which is how 1.2.0 decoded, and once as shipped. The order alternates from clip to clip.
* As shipped, Buddy raises its own process to above-normal priority for the length of the decode and puts
  it back afterwards, also after an error. A process that is not at normal priority is left alone.
* "As it was" is the PC as the run found it, with other programs already using the processor. It is not
  a calm PC: the plain decode was slower there than under the synthetic load. No calm PC was measured.
* A second run, taken while another program held the PC at 100%, repeated the result: 18.40 s to 1.50 s as it was and 25.78 s to 1.71 s with every core busy, shipped faster and same text on 6 of 6 clips in both rows (`results_r2/latency_load_busy_rerun.json`).
* "Every core busy" adds one busy loop per logical processor at normal priority. The loops end by
  themselves.
* A dictation shorter than 15 s is still decoded after the key is released. A longer one is decoded in
  parts while the key is held: see the next section.

### Long dictations: release to text

| Language, engine | The PC | Recordings | Length (mean) | Said | Processor busy | One piece, as 1.2.0 did | In parts, after release | In parts, while held (shipped) | Parts that were text at release |
|---|---|---|---|---|---|---|---|---|---|
| English, Whisper base.en | As it was | 2 | 56 s | 227 words | 93% | 13.1 s, 116 wrong | 8.0 s, 21 wrong | 1.4 s, 21 wrong | 4 of 6 |
| English, Whisper base.en | Every core busy | 1 | 57 s | 137 words | 100% | 31.6 s, 60 wrong | 10.0 s, 10 wrong | 2.7 s, 10 wrong | 2 of 3 |
| Chinese (Mandarin), SenseVoice | As it was | 2 | 82 s | 489 characters | 99% | 19.9 s, 52 wrong | 3.8 s, 44 wrong | 0.2 s, 44 wrong | 6 of 7 |
| Chinese (Mandarin), SenseVoice | Every core busy | 1 | 61 s | 215 characters | 100% | 19.3 s, 29 wrong | 2.5 s, 28 wrong | 0.3 s, 28 wrong | 2 of 3 |
| Indonesian, Omnilingual | As it was | 2 | 81 s | 241 words | 99% | 43.2 s, 223 wrong | 12.9 s, 83 wrong | 2.4 s, 83 wrong | 6 of 8 |
| Indonesian, Omnilingual | Every core busy | 1 | 76 s | 105 words | 100% | 65.4 s, 42 wrong | 11.2 s, 31 wrong | 1.9 s, 31 wrong | 3 of 4 |
| Japanese, Dolphin | As it was | 1 | 85 s | 333 characters | 100% | 11.0 s, 48 wrong | 3.7 s, 46 wrong | 0.5 s, 46 wrong | 3 of 4 |
| Spanish, Parakeet V3 | As it was | 1 | 66 s | 134 words | 100% | 15.5 s, 3 wrong | 5.4 s, 4 wrong | 1.1 s, 4 wrong | 2 of 3 |

6 clips in a recording, 4 decode threads on 8 logical processors, 8 busy loops for the loaded rows.

* `latency_hold.py` joins six FLEURS clips of one language into one recording of 56 to 94 seconds, with a
  second of silence between the clips, and turns it into text three ways. Each way is timed from the
  moment the key is let go, and which way goes first rotates from recording to recording.
  * *One piece* is what 1.2.0 did: the whole recording goes to the engine at once, at normal priority.
  * *In parts, after release* cuts the recording at its pauses and decodes the parts one after the other.
  * *In parts, while held* is what Buddy does now. The recording is played to it at the speed it was
    spoken, and each part is decoded as soon as it ends, while the speaker goes on. Only the last part is
    left when the key is let go.
* A cell is the mean time from release to text, then the words or characters that came out wrong, of
  those in the "Said" column. Both ways of cutting gave the same number of errors in every row. A row is
  one or two recordings, so read the size of a difference and not its decimals.
* A part ends at the first pause of 0.7 s once it is 15 s long, and at 25 s if nobody pauses. A
  dictation shorter than 15 s is not cut: it is decoded in one piece after release, as before. A part
  after the first takes up to 2 s of the pause before it along, so a pause of up to 2.25 s loses no
  audio, and the longest part is 27.25 s, under Whisper's 30 s.
* The parts also bring back text that was lost. Whisper drops what comes after 30 seconds, in 1.2.0 too,
  and Omnilingual falls apart on long audio: see the English and Indonesian rows. Auto-detect uses
  Whisper as well. Parakeet V3 takes a minute of speech in one piece, so for Spanish the parts only gain
  speed; here they cost one word.
* The clips in a recording come from different speakers, so each is first scaled to the same speech level
  (the 90th percentile of its 30 ms frames). The voice detector judges speech against what it heard
  before. In one-off checks that are not among these result files, 2 of 13 recordings joined as recorded
  lost clips to it, and none of the 13 did once levelled. A clip played again after itself was still
  found at a fifth of its level in 7 of 8 clips, and was mostly lost at a twentieth in 6 of 8.
* So speech much quieter than what came before it can be left out. That was already so at the start and
  the end of a dictation. Now it also holds inside a pause of more than 2.25 s between two parts.
* "Processor busy" is the whole PC while the recording played, Buddy's own decoding included. "As it was"
  is the PC as the run found it, with other programs using the processor. It is not a calm PC, and no
  calm PC was measured. "Every core busy" adds one busy loop per logical processor; the loops end by
  themselves.
* Spanish and Japanese have ten clips here, enough for one recording, so they have no loaded row.
* Buddy sets the voice detector to split speech that has no pauses after 10 s; sherpa-onnx's own setting
  is 20 s. Measured once with the real detector, the 20 s setting gave stretches of 23 to 25 s, and the
  10 s setting gave 13 to 19 s at the longest.

### First use: unpacking the model

| Model | Archive | Kept | Unpacked | Processor busy | As 1.2.0 did, mean (runs) | As shipped, mean (runs) | Same files |
|---|---|---|---|---|---|---|---|
| Dolphin | 80.7 MB | everything | 4 files, 104.4 MB | 100% | 91 s (92, 89) | 52 s (56, 48) | yes |
| Whisper base.en | 208.6 MB | only `*int8.onnx` and `*tokens.txt` | 3 files, 160.6 MB | 100% | 377 s (378, 376) | 235 s (244, 226) | yes |

2 runs each way on 8 logical processors; which way goes first alternates from run to run.

* `unpack_time.py` unpacks one downloaded archive two ways and times each. *As 1.2.0 did* reads the
  archive twice, once to list what is in it and once to write the files. *As shipped* reads it once,
  writes into a folder beside the final one, and renames that folder when every file is there. Every
  unpacked file is hashed, and both ways gave byte-identical files in every run.
* This time comes after the download, the first time a language is used: the model is ready only when the
  unpack is complete. Whisper base.en is the model for English, so on this busy PC a new user has about
  four minutes of unpacking, where 1.2.0 took more than six.
* One pass saved 43% for Dolphin and 38% for Whisper base.en. It is not twice as fast.
* The unpack is a single thread and shared the processor with other programs: every run was measured at
  100% busy. No calm PC was measured, so these are the times of a busy PC.
* There are two runs each way for a model, so read the size of a difference and not its decimals.

### Reply language on a live brain

`reply_language.py` asks Buddy's free cloud brain (Llama 4 Scout) one question, "where do I save this
under a new name?", in 12 languages plus an Indonesian question that asks for an English answer. The screen
is a drawn English Notepad with the File menu open, and the system prompt is Buddy's own: `SYSTEM_PROMPT`
+ `language_rule(voice input language)` + the action rules. A reply counts as pointing right when its
pointer lands on the "Save As..." row.

| Run | Replies | Same language as the question | Non-English answered in that language | Tour ends on Save As | Ends somewhere else | No pointer |
|---|---|---|---|---|---|---|
| 1.2.0 prompt, 2 rounds | 26 | 20 | 16 of 22 | 20 | 6 | 0 |
| 1.3.0 prompt, 2 rounds | 26 | 26 | 22 of 22 | 12 | 14 | 0 |
| 1.3.1 (second look), 1 round | 13 | 13 | 11 of 11 | 13 | 0 | 0 |

* 1.2.0 answered Malay, Spanish and French questions in English. The 1.3.0 language rule fixed that: every
  reply came back in the language of the question.
* Answering in another language cost pointing. The model then often writes the menu path as two tags on the
  top edge of the screen, `[POINT:70,0:File] [POINT:93,0:Save As]`, so the arrow went to the top-left
  corner. A reworded language line (`--edit`, `results_r2/reply_language_variant_b.json`) did not help:
  8 of 22 non-English replies on Save As.
* 1.3.1 treats a coordinate of exactly 0 as no place (it is the screen's edge, never a control) and then
  takes a second look: first the names among the controls Windows lists, otherwise one more brain call
  in English that asks only where those labels are (`REPOINT_PROMPT`). Given the 15 edge replies of the
  1.3.0 run, that call put the last point on Save As 13 times (`results_r2/repoint_trial.json`).
* The 1.3.1 row runs the shipped code (`--repoint` calls `Buddy._repoint`). Four replies needed the second
  look (Indonesian, Malay, Thai, French) and all four then ended on Save As. On the same 13 replies, the
  1.3.0 parsing ends 9 on Save As and 4 at the top edge.
* Small sample: one screen, one question, 13 replies for the 1.3.1 row. The drawn screen has no Windows
  controls, so the name lookup was not exercised here; it is covered by a unit test. With Ollama on the
  processor only, Buddy does not make the second call (it would take about a minute) and shows no pointer
  instead of a wrong one.
* Pointing is scored on where the tour ends, because "File, then Save As" is a correct tour. The script
  also stores the first tag's verdict (`points_at_save_as`).

## Talk mode: turns and barge-in
`talk_turns.py` measures the two things a spoken conversation needs: knowing when you have finished (so Buddy neither
cuts in nor leaves a long silence) and stopping when you talk over it. Raw rows: `results_talk/talk_turns.json`.

Run 2026-11-14 16:41 (parts a, b) and 2026-11-14 16:13 (parts c, d) on Intel64 Family 6 Model 189 Stepping 1, GenuineIntel, 8 logical CPUs, Windows 10.

### (a) Turns: finished or not (English, real voice detector + whisper-en on Kokoro speech)

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

### (b) One decision

CPU load during the run: {'mean_percent': 89.8, 'max_percent': 100.0, 'logical_cpus': 8}.

| what | p50 | p95 | n |
|---|---|---|---|
| rules alone, given the words (what Jev is given) | 0.0027 ms | 0.0048 ms | 4000 |
| speech engine (whisper-en) + rules | 0.46 s | 0.874 s | 55 |

Jev (published by the vendor, not measured by us): about 300 ms per decision, vendor cloud only, $0.042 per million input tokens.

### (c) Barge-in (SIMULATED loudspeaker-to-microphone path)

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

### (d) Voice on the real output device (silent samples)

Not measured: the output device did not start.

How it was measured, and what that does and does not show:
* Part (a) plays Kokoro speech (`am_michael`) through the shipped Silero voice detector, Whisper base.en and
  `keyit_talk.TurnTaker`. An unfinished clip is cut mid-sentence, followed by a pause and then the rest; it counts as
  held when Buddy does not take the turn inside that pause. Balanced accuracy is the mean of the two middle columns.
  The rules were tuned on a separate DEV list (`en_dev`), not on these clips.
* "With compute under this load" adds the time of the Whisper decode, which blocks the loop: a turn counts at
  max(fired, detector + decode). The PC was at about 90% CPU load from other work, so this row and part (b) are
  worst-case; they were not measured on a quiet PC.
* Our own target for one decision was 300 ms (p95). The rules take 0.005 ms, but with speech recognition it is 0.87 s
  here, so the target is **missed**.
* Decoding less audio does not fix it. A separate probe on 36 of the clips, at 100% CPU load, gave Whisper only the
  end of each one: whole clip 0.63 s (p95), last 2.0 s 0.50 s, last 1.2 s 0.43 s. The decode has a fixed cost, and the
  short tail also loses words. Meeting 300 ms needs a streaming recognizer or a quiet PC; neither was tried.
* The 10 unfinished clips Buddy cut too early ended in "to", "that", "could", "called", "before", "rather", or were
  misheard by Whisper ("the" as "they", "my" as "mine"). Whisper puts a full stop on everything, so punctuation carries
  no signal.
* Part (c) is a simulation: Buddy's voice is passed through a synthetic room echo at the given level and mixed with the
  user's clip. No real loudspeaker, room or microphone was used. Parts (c) and (d) were run before the echo was switched
  to an FFT convolution (same numbers, faster).
* Part (d) did not run: the output device did not start in the bench process, so how fast the voice stops on a real
  sound device is unmeasured.
* Jev was not run. Its figures are quoted from the vendor's pages listed under `jev.sources` in the JSON.

Reproduce (Kokoro and the speech models must already be in `%APPDATA%\Buddy`; the first run synthesizes the clips, which
took 11 s each here):

    python bench\talk_turns.py --cache <folder for clips> --only abcd
