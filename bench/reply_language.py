"""Does Buddy answer in the language of the question? Asks a live brain (default: Buddy's free cloud brain).

The screen is a drawn English Notepad with the File menu open, the way a Windows PC in an English-language
office looks. The same question ("where do I save this under a new name?") is asked in every language the
1.3.0 picker measured, with Buddy's real system prompt: SYSTEM_PROMPT + language_rule(voice input language)
+ the action rules. Each reply is checked for its language (script for Chinese, Japanese, Korean, Thai;
marker words for the Latin-script languages) and for a [POINT] tag that lands on "Save As...".

    python bench/reply_language.py --code . --out bench/results_r2/reply_language.json
"""
import argparse
import base64
import io
import json
import re
import sys
import time
from pathlib import Path

WIDTH, HEIGHT = 1280, 800
SAVE_AS_BOX = (8, 196, 268, 228)            # pixels of the "Save As..." menu row

QUESTIONS = [
    # (case, voice input language code from the picker, question, expected reply language)
    ("en", "en-US", "Where do I save this file under a new name?", "en"),
    ("id", "id-ID", "Di mana saya menyimpan file ini dengan nama baru?", "id_ms"),
    ("ms", "ms-MY", "Di manakah saya boleh simpan fail ini dengan nama baharu?", "id_ms"),
    ("zh", "zh-CN", "我在哪里把这个文件另存为一个新名字？", "zh"),
    ("yue", "yue", "我喺邊度可以將呢個檔案另存做新名？", "zh"),
    ("ja", "ja-JP", "このファイルを別の名前で保存するにはどこを押せばいいですか？", "ja"),
    ("ko", "ko-KR", "이 파일을 새 이름으로 저장하려면 어디를 눌러야 하나요?", "ko"),
    ("th", "th-TH", "ฉันจะบันทึกไฟล์นี้เป็นชื่อใหม่ได้ที่ไหน", "th"),
    ("vi", "vi-VN", "Tôi lưu tệp này với tên mới ở đâu?", "vi"),
    ("de", "de-DE", "Wo speichere ich diese Datei unter einem neuen Namen?", "de"),
    ("es", "es-ES", "¿Dónde guardo este archivo con otro nombre?", "es"),
    ("fr", "fr-FR", "Où est-ce que j'enregistre ce fichier sous un autre nom ?", "fr"),
    ("id_asks_en", "id-ID", "Di mana saya menyimpan file ini dengan nama baru? Tolong jawab dalam bahasa Inggris.", "en"),
]

MARKERS = {
    "en": {"the", "click", "to", "and", "menu", "you", "then", "on", "file"},
    "id_ms": {"klik", "untuk", "dan", "di", "pada", "lalu", "kemudian", "anda", "pilih", "menu", "ini", "dengan"},
    "vi": {"nhấp", "chọn", "vào", "và", "để", "bạn", "tệp", "rồi"},
    "de": {"klicken", "sie", "und", "auf", "die", "dann", "unter", "speichern", "datei"},
    "es": {"haga", "clic", "en", "el", "para", "y", "luego", "archivo", "guardar", "como"},
    "fr": {"cliquez", "sur", "le", "pour", "et", "puis", "fichier", "enregistrer", "sous", "vous"},
}
SCRIPTS = {
    "zh": re.compile(r"[\u4e00-\u9fff]"),
    "ja": re.compile(r"[\u3040-\u30ff]"),
    "ko": re.compile(r"[\uac00-\ud7af]"),
    "th": re.compile(r"[\u0e00-\u0e7f]"),
}
POINT = re.compile(r"\[\s*POINT\s*:\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


def draw_screen():
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    pen = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("segoeui.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    pen.rectangle((0, 0, WIDTH, 34), fill=(243, 243, 243))
    pen.text((12, 7), "Untitled - Notepad", fill="black", font=font)
    pen.rectangle((0, 34, WIDTH, 66), fill=(250, 250, 250))
    for x, word in zip((12, 70, 124, 200, 262), ("File", "Edit", "Format", "View", "Help")):
        pen.text((x, 40), word, fill="black", font=font)
    pen.rectangle((4, 66, 272, 360), fill=(249, 249, 249), outline=(200, 200, 200))
    rows = ["New        Ctrl+N", "New Window", "Open...    Ctrl+O", "Save       Ctrl+S",
            "Save As...  Ctrl+Shift+S", "Page Setup...", "Print...   Ctrl+P", "Exit"]
    for index, row in enumerate(rows):
        pen.text((20, 72 + index * 32), row, fill="black", font=font)
    pen.text((300, 120), "Meeting notes: send the BOM to purchasing.", fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def reply_language(text):
    words = re.findall(r"[^\W\d_]+", text.lower())
    for name, pattern in SCRIPTS.items():
        if len(pattern.findall(text)) >= 4:
            if name == "zh" and SCRIPTS["ja"].search(text):
                return "ja"
            return name
    if re.search(r"[ạảấầẩẫậắằẳẵặẹẻẽếềểễệịỉọỏốồổỗộớờởỡợụủứừửữựỳỷỹđơư]", text.lower()):
        return "vi"
    scores = {name: sum(word in markers for word in words) for name, markers in MARKERS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "unknown"


def lands_on_save_as(text):
    match = POINT.search(text)
    if not match:
        return None
    x = float(match.group(1)) / 1000 * WIDTH
    y = float(match.group(2)) / 1000 * HEIGHT
    left, top, right, bottom = SAVE_AS_BOX
    return left - 10 <= x <= right + 10 and top - 12 <= y <= bottom + 12


def point_on_save_as(point):
    """Does a parsed Buddy point (x, y, label on the 0..1000 scale) land on "Save As..."?"""
    x = point[0] / 1000 * WIDTH
    y = point[1] / 1000 * HEIGHT
    left, top, right, bottom = SAVE_AS_BOX
    return left - 10 <= x <= right + 10 and top - 12 <= y <= bottom + 12


def buddy_points(keyit_buddy, keyit_free, client, reply, png):
    """The points Buddy itself would show for this reply: parse_reply, then (1.3.1+) the second look
    for tags on the screen's edge, through the real Buddy._repoint. The drawn screen has no Windows
    controls to find by name, so the second look always asks the brain."""
    points = keyit_buddy.parse_reply(reply)[1]
    labels = keyit_buddy.edge_labels(reply) if hasattr(keyit_buddy, "edge_labels") and not points else []
    if not labels:
        return points, False
    buddy = keyit_buddy.Buddy.__new__(keyit_buddy.Buddy)
    buddy.generation = 0
    buddy.client = lambda: client
    buddy._stream = lambda messages: client.chat_stream(keyit_free.MODEL, messages, timeout=90)
    return buddy._repoint(labels, png, None, 0), True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", required=True, help="Buddy repo root")
    parser.add_argument("--out", required=True)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--edit", help="JSON file of [old, new] pairs applied to the system prompt (prompt trials)")
    parser.add_argument("--cases", help="comma-separated case names to ask (default: all)")
    parser.add_argument("--repoint", action="store_true",
                        help="also score what Buddy shows after its second look at edge points (1.3.1+)")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.code) / "keyit"))
    import keyit_actions
    import keyit_buddy
    import keyit_free

    edits = json.loads(Path(args.edit).read_text(encoding="utf-8")) if args.edit else []
    wanted = set(args.cases.split(",")) if args.cases else None
    png = draw_screen()
    client = keyit_free.FreeClient()
    client.warm_up()
    rows = []
    for round_index in range(args.rounds):
        for case, code, question, expected in QUESTIONS:
            if wanted and case not in wanted:
                continue
            rule = getattr(keyit_buddy, "language_rule", lambda _code: "")(code)      # 1.2.0 has no language_rule
            system = keyit_buddy.SYSTEM_PROMPT + rule + "\n" + keyit_actions.ACTION_RULES
            for old, new in edits:
                if old not in system:
                    raise SystemExit(f"prompt edit not found: {old[:60]}")
                system = system.replace(old, new)
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": question, "images": [base64.b64encode(png).decode()]},
            ]
            started = time.perf_counter()
            try:
                reply = "".join(client.chat_stream(keyit_free.MODEL, messages, timeout=90))
                error = ""
            except Exception as problem:
                reply, error = "", str(problem)
            found = reply_language(reply) if reply else "none"
            rows.append({
                "round": round_index + 1,
                "case": case,
                "voice_input": code,
                "question": question,
                "expected": expected,
                "reply_language": found,
                "same_language": found == expected,
                "points_at_save_as": lands_on_save_as(reply),
                "buddy_points": None,
                "second_look": False,
                "buddy_ends_on_save_as": None,
                "seconds": round(time.perf_counter() - started, 1),
                "reply": reply,
                "error": error,
            })
            if args.repoint and reply:
                shown, second_look = buddy_points(keyit_buddy, keyit_free, client, reply, png)
                rows[-1]["buddy_points"] = [list(point) for point in shown]
                rows[-1]["second_look"] = second_look
                rows[-1]["buddy_ends_on_save_as"] = point_on_save_as(shown[-1]) if shown else None
            print(case, found, "OK" if found == expected else "MISS", rows[-1]["points_at_save_as"],
                  rows[-1]["second_look"], rows[-1]["buddy_ends_on_save_as"], flush=True)
            time.sleep(2)
    answered = [row for row in rows if row["reply"]]
    summary = {
        "brain": "free cloud brain (" + keyit_free.CHAT_URL + ")",
        "questions": len(rows),
        "answered": len(answered),
        "same_language": sum(row["same_language"] for row in answered),
        "pointed_at_save_as": sum(bool(row["points_at_save_as"]) for row in answered),
        "had_point_tag": sum(row["points_at_save_as"] is not None for row in answered),
    }
    if args.repoint:
        summary["second_looks"] = sum(row["second_look"] for row in answered)
        summary["buddy_ends_on_save_as"] = sum(bool(row["buddy_ends_on_save_as"]) for row in answered)
        summary["buddy_points_elsewhere"] = sum(row["buddy_ends_on_save_as"] is False for row in answered)
        summary["buddy_no_pointer"] = sum(row["buddy_ends_on_save_as"] is None for row in answered)
    Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1),
                              encoding="utf-8", newline="\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
