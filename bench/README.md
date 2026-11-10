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
