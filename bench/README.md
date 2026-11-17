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
cuts in nor leaves a long silence) and stopping when you talk over it. Raw rows: `results_talk/talk_turns.json` (run A,
the tables below) and `results_talk/talk_turns_run_b.json` (run B, the same bench 35 minutes later).


Run 2026-11-16 16:40 (parts a, a2, b) and 2026-11-16 13:13 (parts c, d) on Intel64 Family 6 Model 189 Stepping 1, GenuineIntel, 8 logical CPUs, Windows 10.

### (a) Turns: finished or not (English, real voice detector + whisper-en on Kokoro speech)

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

### (a2) Turn repair: the speaker adds to something that sounded finished

7 two-part questions (whisper-en); part two starts after the pause. Whole = the question Buddy ends up with has every scripted word in order; word recall = share of the scripted words in it. Audio timeline.

| pause | answered before part two | then repaired: whole | word recall | held as one turn: whole | word recall |
|---|---|---|---|---|---|
| 0.5 s | 57% (n=4) | 50% | 94% | 67% | 97% |
| 0.8 s | 100% (n=7) | 71% | 97% | - | - |
| 1.2 s | 100% (n=7) | 71% | 97% | - | - |

The session only joins the parts if Buddy has been audible for less than 0.5 s when the speaker goes on; after that it is a new question. How soon Buddy is audible depends on the model that answers and is not measured here.

### (b) One decision

CPU load during the run: {'mean_percent': 90.8, 'max_percent': 100.0, 'logical_cpus': 8}.

| what | p50 | p95 | n |
|---|---|---|---|
| rules alone, given the words (what Jev is given) | 0.0029 ms | 0.0047 ms | 6400 |
| speech engine (whisper-en) + rules | 0.386 s | 1.361 s | 201 |
| the same in the real-time pass | 0.354 s | 4.1 s | 201 |

Read-ahead: the read starts 0.1 s into a pause and the shortest gap is 0.3 s, so the first 0.2 s of a read cost the speaker nothing. 0 of 201 reads were thrown away because the speaker had gone on.
Finished turns in real time: taken p50 0.639 s, p95 0.834 s after the last sound; that is p50 0.043 s, p95 0.281 s later than on the audio timeline (n=87).

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

EchoGate.hear compute per 30 ms tick: p95 0.1191 ms.

### (d) Voice on the real output device (silent samples)

Device: Headphones (Realtek(R) Audio) (44100 Hz); 8 rounds; the samples played were silence, so nothing was audible.

| what | p50 | p95 | max | mean | n |
|---|---|---|---|---|---|
| pause requested to silent | 0.05 s | 0.05 s | 0.05 s | 0.05 s | 8 |
| resume requested to samples flowing | 0.145 s | 0.371 s | 0.371 s | 0.144 s | 8 |
| stop requested to device idle | 0.201 s | 3.317 s | 3.317 s | 0.586 s | 8 |

How it was measured, and what that does and does not show:
* Part (a) plays Kokoro speech (`am_michael`) through the shipped Silero voice detector, Whisper base.en (4 threads) and
  `keyit_talk.TurnTaker`. An unfinished clip is cut mid-sentence, followed by a pause and then the rest; it counts as
  held when Buddy does not take the turn inside that pause. Balanced accuracy is the mean of the two middle columns.
* There are four lists, each 24 finished and 24 unfinished utterances, all by the same author. The first is the list
  from the 2026-11-14 run, the second was first read in the round 2 run and the third in the round 3 run. The rules of
  this round (a fourth kind, "leaning", with a 0.9 s gap; a sentence that ends on "I", "we", "they"; "the folder
  called") were written from the misses on those three lists and the 0.9 s gap was chosen on them, so their 95.8%, 92.4%
  and 93.1% are **not** blind figures. The fourth list was written after these rules, by the person who wrote the rules,
  and had never been synthesized or read before run A: 90.3% on the audio timeline. The third list repeats three
  finished sentences of the first ("How do I merge these two cells?", "I think the second option is better.", "Yes, go
  ahead.") and one unfinished one ("After that we should probably"); all four were handled correctly. The lists are
  parallel forms of each other, not a sample of real talk, and none is blind for any later change.
* What the new rules bought, on the same reads. `talk_rules_ab.py` takes run A's reads and works out what the rules
  of the previous revision (`b0e364a`) would have done with them. It is derived from run A on the audio timeline, **not
  a second run**; nothing was read again (`results_talk/talk_rules_ab.json`).

  | list | old rules | new rules | unfinished held, old to new | finished taken in time, old to new |
  |---|---|---|---|---|
  | first | 89.6% | 95.8% | 57 to 66 of 72 | 24 to 24 of 24 |
  | second | 88.9% | 92.4% | 59 to 64 of 72 | 23 to 23 of 24 |
  | third | 89.6% | 93.1% | 57 to 62 of 72 | 24 to 24 of 24 |
  | fourth (not used to write the rules) | 85.4% | 90.3% | 54 to 61 of 72 | 23 to 23 of 24 |
  | all | 88.4% | 92.9% | 227 to 253 of 288 | 94 to 94 of 96 |

  16 clips changed. On the fourth list three cut-off clips are now held through all three pauses, "How far along are
  we?" is no longer read as unfinished, and one finished sentence got **worse**: "I have seen this error before." now
  counts as leaning and was taken 1.005 s after the last sound, 5 ms too late. The 0.9 s gap is marginal for 0.8 s
  pauses: the leaning clips are held at 0.5 s and 0.8 s and taken at 0.93 to 0.98 s, before a 1.2 s pause is over.
* "With compute under this load" is measured, not estimated. Each clip is played a second time against the wall clock,
  with the speech engine reading on its own thread while the audio goes on, as talk mode runs it; a turn counts when it
  was really taken. It was run twice on the same clips (the audio-timeline rows are identical in both runs):

  | | run A (16:40) | run B (17:15) |
  |---|---|---|
  | CPU load, mean | 90.8% | 92.0% |
  | Buddy with compute: balanced accuracy | 89.8% | 89.2% |
  | finished taken in time / unfinished not cut | 90% / 90% | 88% / 90% |
  | mean delay, finished | 0.642 s | 0.682 s |
  | with compute by list (first, second, third, fourth) | 89.6 / 91.0 / 91.0 / 87.5% | 92.4 / 90.3 / 86.1 / 88.2% |
  | best fixed pause by list (the same in both runs) | 72.9 / 72.2 / 75.7 / 72.2% | 72.9 / 72.2 / 75.7 / 72.2% |
  | best fixed pause, then waits for the same read (computed) | 70.7% | 71.0% |
  | one decision, speech engine + rules, p50 / p95 | 0.386 s / 1.361 s | 0.395 s / 0.662 s |
  | the same in the real-time pass, p50 / p95 | 0.354 s / 4.1 s | 0.363 s / 5.021 s |
  | finished turns not taken within 1.0 s | 10 of 96 | 11 of 96 |
  | of those, never taken | 9 | 8 |

  In both runs Buddy with compute is above the best fixed pause on every one of the four lists. In round 3 it was below
  it on the third list (73.6% against 75.7%).
* Why it moved. With compute: 57.2% in the first run of 2026-11-16, 66.3% in round 2 (96 clips), 82.6% in round 3 (144
  clips), 89.8% and 89.2% now (192 clips). Round 3 came from holding above-normal priority for the whole conversation
  instead of only during a read. A separate A/B on 36 finished clips, the two settings interleaved, at about 91% load:

  | priority | taken within 1.0 s | never taken | taken, p50 | read back, p50 | p90 |
  |---|---|---|---|---|---|
  | above normal only during a read | 26 of 36 | 4 | 0.811 s | 0.685 s | 5.07 s |
  | above normal for the whole clip (adopted) | 34 of 36 | 2 | 0.693 s | 0.509 s | 0.684 s |

  This round's gain comes from the rules (table above) and from a fix to that priority code: when a read was already
  running as the conversation began, the priority fell back to normal as soon as that read came back. `talk_priority.py`
  starts a session in a second process and reads its priority class from outside, as Task Manager would: before the fix
  1 of 15 steps was wrong ("talking, the read is back" showed normal), after it 0 of 15
  (`results_talk/talk_priority_before_fix.json`, `results_talk/talk_priority.json`). It checks the session thread and
  the priority code in a separate process, **not the installed app**: no hotkey is pressed and no window is shown.
* The finished turns that were not taken, and why. 10 of 96 (run A; 3, 2, 2, 3 by list) and 11 of 96 (run B; 2, 2, 4,
  3) were not taken within 1.0 s. Nine and eight of them were never taken because one read took 2.8 to 6.3 s instead of
  0.4 s. In round 3 the cause of these stalls was not found. It is now: **the laptop's firmware slows the processor
  down for seconds at a time**. `talk_stalls.py` reads the same clips over and over and writes down, for every read,
  the wall time, the processor time, the page faults and the "% Processor Performance" counter (how fast the cores are
  allowed to run; above 100% is turbo).

  | variant, 30 reads each (stall = a read over 1.0 s) | p50 | p95 | max | stalls |
  |---|---|---|---|---|
  | live: as shipped, 4 threads, read on the reader thread | 0.354 s | 3.895 s | 4.054 s | 5 |
  | plain: the same clip read on the main thread (control) | 0.378 s | 0.913 s | 5.205 s | 1 |
  | two threads | 0.56 s | 4.081 s | 4.725 s | 4 |
  | one thread | 0.923 s | 1.364 s | 6.316 s | 11 (10 of them only slow, at full speed) |

  The 99 quick reads (1.0 s or less) ran with the cores at 112% to 175% of nominal speed (median 157%). **All 9 reads
  over 1.5 s ran at 18% to 71%**: seven at 18% to 20%, one at 38%, one at 71%. None of the nine had a hard page fault,
  so it is not Windows fetching memory back from disk, and the CPU load reading during them was 50% to 89%, so it is not
  a fuller processor either. Earlier on the same day the counter read about 18% with the processor at about 93 °C. In
  a first stage with 100 plain reads and pauses between them there was no stall at all (4 threads: p50 0.293 s, p95
  0.35 s, max 0.381 s). Fewer threads do not help, as the table shows, and Buddy cannot switch the throttling off, so
  this is **not fixed**; a cooler or faster PC should not show it (not measured). Raw rows:
  `results_talk/talk_stalls_stage1.json`, `results_talk/talk_stalls_live.json`. The remaining late turns (1 in run A, 3
  in run B) were taken 1.00 to 2.74 s after the last sound, with reads of 0.39 to 2.2 s.
* Our own target for one decision was 300 ms (p95). The rules take 0.005 ms, but with speech recognition it is 1.36 s
  in run A and 0.66 s in run B (4.1 s and 5.0 s in the real-time pass, where the stalls above land), so the target is
  **missed**. Earlier runs: 1.51 s (round 3), 1.34 s (2026-11-16 10:02, 88% load) and 0.87 s (2026-11-14, 90% load).
* Read-ahead hides part of that wait: the read starts 0.1 s into a pause instead of at the end of it, and no read was
  thrown away in either run (0 of 201). A finished turn that was taken came a median 0.043 s later than the audio alone
  allows in both runs; p95 0.281 s (run A, n=87) and 0.347 s (run B, n=88).
* The two "then waits for the same read" rows are computed, not run: a fixed pause that also had to wait for the read
  Buddy waited for. They are there so the with-compute row is not compared only with a baseline that needs no words.
* Decoding less audio does not fix it. A separate probe on 36 of the clips, at 100% CPU load, gave Whisper only the
  end of each one: whole clip 0.63 s (p95), last 2.0 s 0.50 s, last 1.2 s 0.43 s. The decode has a fixed cost, and the
  short tail also loses words. Measured, not adopted.
* Other engines were probed and none was adopted. These were short probes, not the full bench. The Whisper rows are
  one read of each of the 96 clips of lists one and two with the priority raised; "cut-offs caught" is the share of the
  48 unfinished clips the rules call unfinished on that engine's reads.

  | engine | one read, p50 | p95 | max | cut-offs caught | CPU load |
  |---|---|---|---|---|---|
  | Whisper base.en, 4 threads (shipped) | 0.792 s | 1.331 s | 1.46 s | 70.8% | 67% |
  | Whisper base.en, 4 threads, tail padding 50 | 0.587 s | 1.019 s | 1.26 s | 35.4% (finished kept 81.2%) | 88% |
  | Whisper tiny.en, 4 threads | 0.601 s | 1.300 s | 3.84 s | 66.7% | 78% |
  | Whisper tiny.en, 2 threads | 0.295 s | 1.605 s | 2.68 s | 62.5% | 75% |
  | Whisper tiny.en, 1 thread | 0.494 s | 0.649 s | 3.31 s | 66.7% | 86% |
  | Whisper tiny.en, 4 threads, tail padding 50 | 0.460 s | 1.556 s | 2.47 s | 64.6% (finished kept 87.5%) | 81% |

  Whisper tiny.en does not reach 300 ms at p95 either and catches fewer cut-offs. Probed on 2026-11-16 before that:

  | engine | one decision, p95 (busy PC) | accuracy | why not |
  |---|---|---|---|
  | Whisper base.en + rules (shipped) | 1.36 s and 0.66 s in two runs (4.1 and 5.0 s in real time) | 92.9% measured | misses 300 ms |
  | Streaming Zipformer en-20M + rules | 0.07 to 0.18 s | about 78% (estimate) | drops the start of utterances, no punctuation |
  | Moonshine tiny en int8 + rules | 0.52 to 0.74 s (0.14 s when the PC was less busy) | about 77 to 82% (estimate) | invents an ending for cut-off speech |
  | Smart Turn v3.2 (audio only, no words) | 0.33 s on 1 thread | 40 of 40 finished clips right, 11 of 40 cut-off clips right at threshold 0.5 | calls most cut-off clips complete |

  The Smart Turn result says little about real speech: these clips are a synthetic voice reading a sentence that was cut
  in the text, and it may well end them with a falling tone (not tested).
* The 20 unfinished clips Buddy still cut too early (taken before a 1.2 s pause was over; 4, 5, 6 and 5 by list; round
  3: 19 of 72). Two were read as complete and taken 0.38 to 0.41 s into the pause: "Paste it right below the" (heard as
  "that") and "Could you forward this to" (heard as "too?"). Nine were read as neutral and taken at 0.73 to 0.83 s;
  eight of those are mis-hearings of the last word that the words alone cannot catch: "my" heard as "mine." (three
  times), "to, hmm" as "hung.", "change the" as "them.", "open the" as "things.", "above the" as "that." and "and, uh"
  as "and up."; the ninth is "Next we could probably". Eight are leaning ("we could", "we should" twice, "before" four
  times) and were taken at 0.93 to 0.98 s, so they are held through a 0.5 s and a 0.8 s pause but not a 1.2 s one. One
  was read as unfinished ("to, uh") and taken at 1.21 s. Whisper puts a full stop on cut-off speech, so punctuation
  carries no signal; telling "my" from "mine." needs the melody of the voice, which Buddy does not use.
* The two finished turns missed on the audio timeline: "Open the calendar." (second list, read as unfinished, taken
  at 1.67 s) and "I have seen this error before." (fourth list, leaning, 1.005 s).
* Parts (a) and (a2) disagree at a 0.5 s pause (43% against 57%, one clip of 7) and the cause is known. Part (a) counts
  a turn as taken before the speaker went on when it fires earlier than the pause plus `ONSET` = 0.15 s, the time
  allowed for the voice detector to notice the speaker again. For "I want to change the background" the turn fired
  0.667 s after part one, 0.167 s after part two began: the detector needed about 0.17 s. With `ONSET` = 0.2 s part (a)
  gives 57% as well.
* `ONSET` was frozen at 0.15 s before round 3 and the tables above use it. Re-scoring the saved rows of both runs with
  other values (nothing re-measured; set `talk_turns.ONSET` and score the JSON again):

  | `ONSET` | Buddy, audio timeline | with compute, run A / run B | fixed 0.7 s | best fixed pause | closest list: with compute against best fixed |
  |---|---|---|---|---|---|
  | 0.15 s (frozen, used above) | 92.9% | 89.8% / 89.2% | 66.5% | 73.3% | third list, run B: 86.1% against 75.7% |
  | 0.20 s | 91.8% | 88.9% / 88.5% | 66.0% | 66.7% | third list, run B: 85.4% against 67.4% |
  | 0.25 s | 91.5% | 88.7% / 88.0% | 64.9% | 66.5% | third list, run B: 84.7% against 66.7% |
  | 0.30 s | 90.8% | 87.7% / 87.2% | 56.8% | 66.5% | third list, run B: 84.0% against 66.7% |

  No value makes the fixed pause win on any list in either run. The detector's real onset time was seen on one clip
  (about 0.17 s), not measured over all of them, so the headline stays at 0.15 s, the value that is kindest to the
  fixed pause.
* Part (a2) scores the question Buddy ends up with after the speaker adds to something that sounded finished. Buddy
  answered early in 4 of 7 clips at a 0.5 s pause and in 7 of 7 at 0.8 s and 1.2 s. Over those 18 early answers the
  joined question had every scripted word in order in 12 (2 of 4, 5 of 7, 5 of 7) and 96.3% of the words on average.
  Of the 6 misses, 5 are a word Whisper wrote differently ("open" as "opened", "twelve" as "12") and in 1 a word was
  lost ("to"). It is on the audio timeline, and it assumes Buddy had been audible for less than 0.5 s; how soon Buddy
  is audible depends on the model that answers and was not measured.
* Parts (c) and (d) are **from the 13:13 run (round 3) and were not measured again this round**; the echo gate and the
  voice code they exercise were not changed. Part (c) is a simulation: Buddy's voice is passed through a synthetic room echo at the given level and
  mixed with the user's clip. No real loudspeaker, room or microphone was used, and Buddy has no echo canceller. One
  `EchoGate.hear` tick of 50,976 took 17 s in that run.
* Part (d) runs on the real output device, playing silence so nothing is audible. The voice was silent 0.05 s after a
  pause request in all 8 rounds. One of the 8 stops took 3.3 s to leave the device idle (the others about 0.2 s). That
  and the 17 s tick are likely the same throttling as above; this was **not measured**.
* Jev was not run. Its figures are quoted from the vendor's pages listed under `jev.sources` in the JSON.

Reproduce (Kokoro and the speech models must already be in `%APPDATA%\Buddy`; the first run synthesizes the clips, which
took 11 s each here; `--hold` keeps above-normal priority for each clip, as the app does for a conversation). Parts (a),
(a2) and (b) take about 25 minutes:

    python bench\talk_turns.py --cache <folder for clips> --only abcd --hold
    python bench\talk_turns.py --cache <folder for clips> --only ab --hold --out <another folder>
    python bench\talk_rules_ab.py --results bench\results_talk\talk_turns.json --old b0e364a
    python bench\talk_priority.py --out bench\results_talk\talk_priority.json
    python bench\talk_stalls.py --cache <folder for clips> --reads 200
    python bench\talk_stalls.py --cache <folder for clips> --live --reads 30 --variants live,plain,two-thread,one-thread

## Thinking fast and slow
Two benches check System 1 ([keyit/keyit_minds.py](../keyit/keyit_minds.py)): when may it act without the brain, and
is its confidence honest? The numbers in the main README ("Thinking fast and slow: Buddy vs Jev") come from here.
`fast_slow.py` is the bench of Buddy 1.5.0, whose System 1 counted its confidence by hand; its tables below are for
that version. Since 1.6.0 the confidence is trained on simulated use, and `fast_calibration.py` checks it:
[Trained and calibrated by simulation](#trained-and-calibrated-by-simulation).

    python bench/fast_slow.py --out bench/results_fast --real 5 --brain 2

Two lists of typed requests, each labelled with what a careful assistant would do at once without looking at the
screen, or "think first". System 1's confidences (`PRIORS`) are counted on the first list. The second was written before
the first run, was scored once, and no rule was changed after it. Both are English text by the author of the rules: they
are parallel forms, not a sample of real requests. Latest run: 89% CPU load on the office laptop. Raw rows:
[results_fast/fast_slow.json](results_fast/fast_slow.json).

| | First list (rules counted on it) | Held-out list |
|---|---|---|
| Requests | 96 | 94 |
| One-step commands | 53 | 49 |
| Must think first | 43 | 45 |
| Done by System 1 | 48 | 43 |
| Wrong fast actions | 0 | 0 |
| One-step commands done fast | 48 of 53 | 43 of 49 |
| Calibration error (ECE) | 0.067 | 0.061 |

| Rule | What it reads | Stated confidence | Right on the held-out list | Stated minus observed |
|---|---|---|---|---|
| window | close / minimize / maximize / switch to an open window named by its app | 0.958 | 22 of 22 | -0.042 |
| app | open an app Buddy knows | 0.938 | 14 of 14 | -0.062 |
| site | open a web address | 0.900 | 4 of 4 | -0.100 |
| this | the window in front ("minimize this") | 0.875 | 4 of 4 | -0.125 |

No rule claimed more than it delivered, so the whole calibration error is caution. It proves little: none wrong in 44
readings still allows a true error rate of up to 6.6% (95% bound), and 12.7% for the 22 window readings alone.

Left to the brain although one step, on the held-out list: "close this app" (0.875 is under the 0.95 bar for closing),
"close the current window" and "maximize this window please" (System 1 could not tell which window), "could you
minimize excel" (a doubt), "copy this" and "type best regards" (not System 1's job).

| | Measured |
|---|---|
| One decision (38,000 decisions over both lists) | 0.011 ms median, 0.023 ms p95, 2.1 ms slowest |
| Listing the open windows | 2.4 ms median |
| Words to done, System 1: 15 real actions (minimize, maximize, switch to) on a window the bench opened, each checked by asking Windows | 0.014 s median, 0.092 s slowest, 15 of 15 done |
| The same kind of command through the free cloud brain: 12 asks with Buddy's prompt and a drawn screenshot, timed to the end of the reply | 3.35 s median, 2.93 to 4.19 s, 0 errors |
| Same action as System 1 would take, in those 12 replies | 10 of 12 (twice the brain opened File Explorer instead of switching to the open one) |

The System 1 time runs from the words to the action returning; speech and the on-screen bubble are not in it. The brain
time has no screenshot capture and no action in it, and the Smart and Local brains were not timed.

How much practice a habit needs before System 1 does it alone (the brain did the same single step for the same short
phrase, and nobody said "no"):

| Action | Bar | Times |
|---|---|---|
| switch to, minimize, maximize | 0.80 | 3 |
| open | 0.85 | 5 |
| keys (a shortcut) | 0.90 | 8 |
| close | 0.95 | 18 |

These come from the formula `(right + 1) / (seen + 2)`, not from data. In simulated use the habit confidence turned out
cautious rather than calibrated (next section).

### Trained and calibrated by simulation
Since Buddy 1.6.0, how sure System 1 is of a reading comes from a small trained model, `keyit/minds_model.json`
(57 weights, 40 wordings). Real use would take months to give enough examples with a known right answer, so the
examples are simulated, and the result is checked on requests the simulator did not write.

    python bench/simulate.py --requests 5 --seed 1                              # look at a few simulated requests
    python bench/minds_train.py                                                 # train, calibrate, write the model
    python bench/minds_train.py --check                                         # the model file is what training gives
    python bench/fast_calibration.py                                            # rehearsal, no held-out data
    python bench/fast_calibration.py --final --out bench/results_calibration    # the held-out sets, scored once

| Step | Data | Size |
|---|---|---|
| Train | `simulate.py`: a program writes a wish, the windows that are open, a wording and now and then a typing slip. The right answer is known because the request was written from the wish | 30,000 requests, 15,736 readings, 14,862 of them right |
| Train | `data/sim_users.json`, split "train": people played by Llama 4 Scout, who were given a person, the open windows and a wish, and never saw Buddy's rules ([sim_users.py](sim_users.py)) | 636 requests, 116 readings, all right |
| Calibrate (Platt scaling) | The same file, split "calibration": two other people played by the same model | 517 requests, 92 readings, 91 right |
| Test, once | Split "test": four other people played by a different model, Mistral Small 3.1; Microsoft's Voice Access command list; the held-out list of 1.5.0; CLINC150 `test` and `oos_test` (Larson et al. 2019, CC BY 3.0, fetched by the script, not kept in git); 100 simulated requests said by two Kokoro voices and heard by whisper-en ([sim_speech.py](sim_speech.py)) | 6,202 requests |

A "reading" is a request System 1 could read as one of its commands. Both sources of training data count the same in
total, so one language-model reading weighs about as much as 135 program-written ones. That share (0.5) was picked by
leaving one simulated person out at a time, on the training and calibration people only: log loss 0.0611 at a share
of 0.1, 0.0592 at 0.25, 0.0566 at 0.5.

Results, scored once on 17 Nov 2026 after the model was frozen: [results_calibration/fast_calibration.md](results_calibration/fast_calibration.md),
raw rows in [results_calibration/fast_calibration.json](results_calibration/fast_calibration.json). "1.5.0" is the same
code with no model file.

| Held-out set | Requests | One-step commands | Done at once and right (1.5.0 -> trained) | Wrong fast (1.5.0 -> trained) | 95% bound on the wrong share | Readings right | ECE |
|---|---|---|---|---|---|---|---|
| Simulated users (Mistral Small 3.1, four people) | 364 | 191 | 23 -> 42 | 0 -> 0 | 6.7% | 45 of 46 | 0.036 |
| Voice Access command list | 44 | 38 | 29 -> 31 | 0 -> 0 | 9.2% | 32 of 32 | 0.025 |
| Held-out list of 1.5.0 | 94 | 49 | 43 -> 40 | 0 -> 0 | 7.2% | 47 of 47 | 0.075 |
| CLINC150, test + oos_test | 5,500 | 0 | 0 -> 0 | 0 -> 0 | no fast action | none | none |
| Simulated speech, voice af_heart | 100 | 75 | 14 -> 43 | 0 -> 0 | 6.7% | 49 of 53 | 0.051 |
| Simulated speech, voice am_michael | 100 | 75 | 14 -> 45 | 0 -> 0 | 6.4% | 51 of 55 | 0.050 |
| In the simulator (a seed not trained on) | 20,000 | 12,957 | 2,783 -> 9,302 | 183 -> 90 | 1.1% | 9,956 of 10,511 | 0.027 |

The first four sets together: 114 fast actions, none wrong, so under 2.6% with 95% confidence. With the speech sets:
202, none wrong, under 1.5%; but the speech sets are the same 100 simulator requests said twice, so they are not
independent of each other or of the simulator's wording.

Reliability over the six held-out sets together (233 readings, 224 right, ECE 0.028):

| Stated confidence | Readings | Stated (mean) | Right | Overconfident by |
|---|---|---|---|---|
| 0.5 to 0.6 | 12 | 0.546 | 0.500 | +0.046 |
| 0.6 to 0.7 | 7 | 0.687 | 0.571 | +0.116 |
| 0.7 to 0.8 | 2 | 0.718 | 1.000 | -0.283 |
| 0.8 to 0.9 | 9 | 0.876 | 1.000 | -0.124 |
| 0.9 to 1.0 | 203 | 0.983 | 1.000 | -0.017 |

Every action was as right as its bar among the readings that passed it: switch to 26 of 26, minimize 42 of 42, maximize
37 of 37, open 58 of 58, close 39 of 39. One decision took 0.016 ms (median) and 0.039 ms (p95) over the 6,202 requests.

What these numbers do not show:
- Nearly all of it is simulated. The "users" are language models playing people, and the speech is a synthetic voice in
  a silent room heard by the recognizer. No real person's requests and no real microphone are in any set.
- 203 of the 233 readings are in the top bin, so the 0.028 says little about the middle. The two bins that overclaim
  have 12 and 7 readings. Our gate (no bin with 30 or more readings overconfident by more than 0.05) passes because no
  such bin exists, and 0.028 is close to the limit of 0.03 we set beforehand.
- In the simulator itself the middle is overconfident: by 0.43 between 0.7 and 0.8 (248 readings) and by 0.09 between
  0.8 and 0.9 (444). The two simulators disagree about "open settings" and about opening an app that is already open:
  the program assumes the person often means something else, the language-model people nearly always mean the app.
  The trained confidence sits between the two. Most of these readings are under the bars and go to the brain, but
  "go to settings" (0.88) is acted on and the program counts it wrong.
- On the held-out list of 1.5.0 the trained mind does 40 commands where 1.5.0 did 43. It gained "close this app",
  "maximize this window please" and "could you minimize excel", and lost "launch notepad", "open edge", "open microsoft
  edge", "launch excel", "open outlook" and "open explorer": those apps are open in that list's windows, and the
  trained mind is 0.55 to 0.69 sure of opening an app that is already open, under the 0.85 bar.
- On the simulated users System 1 did 42 of 191 one-step commands. It stood down on the others mostly for "nothing I
  know by heart" (49), "I can't tell which window" (41), "I don't know that app by heart" (17) and "two steps" (16).
- The polite words ("can you", "please") come off before reading and get no weight of their own: with weights for them
  the rehearsal log loss was 0.0619, without 0.0611.

Habits in simulated use (200 simulated people, 250 requests each, a dozen phrases of their own; the brain does a phrase
until its habit is sure enough for System 1):

| Brain wrong | Person means something else | Says "no" when wrong | Fast by habit | Wrong fast, per 1,000 requests | Habit confidence stated | Habit right | ECE |
|---|---|---|---|---|---|---|---|
| 0% | 0% | always | 37,402 of 50,000 | 0 | 0.905 | 1.000 | 0.095 |
| 5% | 2% | always | 33,757 | 13.2 | 0.862 | 0.956 | 0.100 |
| 5% | 2% | half the time | 33,324 | 13.2 | 0.851 | 0.934 | 0.095 |
| 10% | 10% | half the time | 23,485 | 45.9 | 0.730 | 0.771 | 0.093 |
| 5% | 2% | never | 33,266 | 13.3 | 0.844 | 0.914 | 0.089 |

The habit confidence states about 0.09 less than it delivers in every world: it is cautious, not calibrated. The wrong
fast actions come almost entirely from the person meaning something else this time, which a count cannot see; saying
"no" barely changes how many there are, it changes how soon the habit is dropped.

`keyit/tests/test_minds.py` covers the rules, the trained model, habits and feedback. It retrains the model and
compares it with the shipped file, checks that Buddy behaves as 1.5.0 when the file is missing or broken, and breaks
the "no" feedback in four ways on purpose to see that each one is caught.

#### Real window actions with the built and the installed app

    python bench/battery.py                                          # the code inside dist/Buddy, in this process
    python bench/battery.py --app "C:/path/to/Buddy"                 # the code inside another Buddy folder
    python bench/battery.py --app "C:/path/to/Buddy" --through-app   # the Buddy running there, by Buddy.exe --ask

`bench/battery.py` opens a small window of its own and gives 19 typed commands about it: minimize, switch to and
maximize three times over, nine other wordings ("hide ...", "bring up ...", "make ... full screen", "could you switch
to ...?"), then close. Before each command it puts the window in a state the command has to change, and afterwards it
asks Windows whether it did. A command is only sent when the app's own System 1 code says it will act at once;
anything else would go to the brain, which takes a screenshot, so it is skipped and listed. It waits for 25 s without
keyboard or mouse use first. Results: [results_battery](results_battery), 17 Nov 2026.

| What ran | Done at once and confirmed by Windows | Handed to the brain (not sent) | Words to done |
|---|---|---|---|
| Code inside the built Buddy 1.6.0, in the bench's process | 19 of 19 | 0 | 0.009 s median, 0.202 s slowest (close) |
| Code inside Buddy 1.5.0, in the bench's process | 11 of 19 | 8 | 0.011 s median, 0.202 s slowest (close) |
| The running, installed Buddy 1.5.0, by `Buddy.exe --ask` (three old wordings three times, then close) | 10 of 10 | not tried | 0.007 s median, 0.204 s slowest (close), by Buddy's own log; 0.89 s median with the start of the `--ask` helper |

What this does and does not show:

- The files that ship in 1.6.0 (code and `minds_model.json`) act on a real window, and wordings 1.5.0 did not know
  are done at once.
- The 19 wordings were picked by the bench's author, nine of them after the training. They are not a sample of what
  people say, so 19 of 19 is not a coverage figure. The held-out sets above are (42 of 191 for the simulated users).
- Closing takes 0.2 s because Buddy waits to see the window go.
- The installed row is Buddy 1.5.0: the installed app updates itself in its own time and was not forced. **Buddy
  1.6.0 has not been checked installed and running.** That row was made by the first copy of this bench, before the
  app folder became an argument.
- Every command was typed. Nobody has spoken one to the installed app at a real microphone yet.
