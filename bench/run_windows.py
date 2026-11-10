"""Baseline B0: Buddy's current fallback on this network (Windows System.Speech, exactly as keyit_buddy does it)."""
import json
import subprocess
import sys
import time

from bench_lib import BENCH, LANGUAGE, all_clips, key_of, summarize

SCRIPT = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$culture = New-Object System.Globalization.CultureInfo('{culture}')
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)
$engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
$engine.SetInputToWaveFile('{path}')
$engine.InitialSilenceTimeout = [TimeSpan]::FromSeconds(3)
$engine.BabbleTimeout = [TimeSpan]::FromSeconds(0)
$engine.EndSilenceTimeout = [TimeSpan]::FromSeconds(1.5)
$engine.EndSilenceTimeoutAmbiguous = [TimeSpan]::FromSeconds(1.5)
$result = $engine.Recognize()
$engine.Dispose()
if ($result -ne $null) {{ Write-Output $result.Text }}
"""

rows = []
for name, path in all_clips():
    key = key_of(path)
    # Buddy's setting is one language (default en-US); give it the right one per clip to be generous
    culture = "zh-CN" if name != "silence" and LANGUAGE.get(key) == "zh" else "en-US"
    started = time.perf_counter()
    result = subprocess.run(["powershell", "-NoProfile", "-Command", SCRIPT.format(culture=culture, path=str(path))],
                            capture_output=True, timeout=60, creationflags=0x08000000)
    seconds = time.perf_counter() - started
    hyp = result.stdout.decode("utf-8", "replace").strip()
    rows.append({"set": name, "key": key, "file": path.name, "hyp": hyp, "seconds": round(seconds, 2)})
    print(name, path.name, round(seconds, 1), hyp, flush=True)

(BENCH / "results").mkdir(exist_ok=True)
(BENCH / "results" / "B0_windows.json").write_text(
    json.dumps({"engine": "B0 Windows System.Speech (current office fallback)", "summary": summarize(rows), "rows": rows},
               ensure_ascii=False, indent=1), encoding="utf-8")
print("SUMMARY", summarize(rows), flush=True)
