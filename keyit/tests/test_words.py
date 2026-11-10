"""Buddy's words: personal dictionary, spoken shortcuts and dictation clean-up (keyit_words)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import keyit_words as words  # noqa: E402

MY_WORDS = """
# work words
SRM
eGreen
PureGMS
ZIRIS
Pega-Vision
Spec Green
my sign off => Best regards, Juwen
my email -> juwen@example.com
"""


def test_parse_words_terms_snippets_and_notes():
    terms, snippets = words.parse_words(MY_WORDS)
    assert terms == ["SRM", "eGreen", "PureGMS", "ZIRIS", "Pega-Vision", "Spec Green"]
    assert snippets == [("my sign off", "Best regards, Juwen"), ("my email", "juwen@example.com")]


def test_terms_fix_spacing_case_and_dots():
    terms, _ = words.parse_words(MY_WORDS)
    fix = lambda text: words.apply_terms(text, terms)
    assert fix("open the s r m page") == "open the SRM page"
    assert fix("Open S.R.M. now") == "Open SRM now"
    assert fix("the e green part survey") == "the eGreen part survey"
    assert fix("set pure gms to rejected") == "set PureGMS to rejected"
    assert fix("this pega vision warning") == "this Pega-Vision warning"
    assert fix("where is the spec green field") == "where is the Spec Green field"


def test_terms_fix_near_misses_of_long_words_only():
    terms, _ = words.parse_words(MY_WORDS)
    assert words.apply_terms("the Zyris power test passed", terms) == "the ZIRIS power test passed"
    assert words.apply_terms("Siris power test", terms) == "ZIRIS power test"
    # ordinary words that merely look similar stay
    assert words.apply_terms("the series of tests", terms) == "the series of tests"
    assert words.apply_terms("a green light", terms) == "a green light"
    # terms never eat parts of longer words
    assert words.apply_terms("the screams", terms) == "the screams"


def test_snippets_expand_whole_phrases_any_case():
    _, snippets = words.parse_words(MY_WORDS)
    assert words.apply_snippets("Thanks. My sign off.", snippets) == "Thanks. Best regards, Juwen"
    assert words.apply_snippets("send it to my email", snippets) == "send it to juwen@example.com"
    assert words.apply_snippets("my signing off", snippets) == "my signing off"


def test_fillers_and_stutters_removed():
    assert words.clean("Um, how do I, uh, export this sheet to PDF?") == "How do I export this sheet to PDF?"
    assert words.clean("open the the file") == "Open the file"
    assert words.clean("I know that that works") == "I know that that works"
    assert words.clean("hmm, er, close it.") == "Close it."
    # "umbrella", "summer", "her" are words, not fillers
    assert words.clean("her umbrella in summer") == "Her umbrella in summer"


def test_spoken_corrections():
    assert words.clean("Send the report at three, no wait, at four o'clock.") == "Send the report at four o'clock."
    assert words.clean("Send it to John, sorry, Mary.") == "Send it to Mary."
    assert words.clean("Meet on Monday, I mean Tuesday.") == "Meet on Tuesday."
    assert words.clean("Meet on Monday, I mean, Tuesday.") == "Meet on Tuesday."
    assert words.clean("The samples ship today. Scratch that. They ship on Friday.") == "They ship on Friday."
    # soft markers without a pause are normal speech
    assert words.clean("I mean it.") == "I mean it."
    assert words.clean("Sorry for the delay.") == "Sorry for the delay."


def test_chinese_fillers_and_text_untouched():
    assert words.clean("嗯，保存按钮在哪里？") == "保存按钮在哪里？"
    assert words.process("帮我打开记事本。", MY_WORDS) == "帮我打开记事本。"


def test_process_modes():
    said = "um, open the e green survey and set pure gms to rejected. my sign off"
    assert words.process(said, MY_WORDS, mode="dictate") == (
        "Open the eGreen survey and set PureGMS to rejected. Best regards, Juwen")
    # questions to Buddy keep spoken shortcuts as said (they are for typing), but get the spelling fixes
    assert words.process(said, MY_WORDS, mode="ask").startswith("Open the eGreen survey and set PureGMS")
    assert "my sign off" in words.process(said, MY_WORDS, mode="ask").lower()
    assert words.process("", MY_WORDS) == ""
    assert words.process("Hello there.", "") == "Hello there."


def test_edit_commands_for_highlighted_text():
    for said in ("Make this shorter.", "please translate to Chinese", "Rewrite it more politely",
                 "fix the grammar", "Summarize this", "翻译成英文", "can you turn this into bullet points"):
        assert words.is_edit_command(said), said
    for said in ("The samples ship on Friday.", "Hello team, the test passed.", "Thanks for the update",
                 "", "make " + "word " * 40):
        assert not words.is_edit_command(said), said


# --------------------------------------------------------------------------- 1.3: other languages, safer rules
BENCH_WORDS = "SRM\nPLM\nBOM\neGreen\nPureGMS\nZIRIS\nPega-Vision\nSpec Green\nVendor Mapping\nNotepad\nOutlook\n"
ENGLISH = frozenset("""type hello team power test passed the a is brightest star called about series starts on
    friday scored twice funded project new store opened report late ready answer was wrong sirius cyrus""".split())


def test_text_that_only_looks_like_a_filler_or_marker_stays():
    # 1.2.0 ate these: 额 is not always "uh", "mm" is a unit, "ER" a name
    assert words.clean("金额是五百元。") == "金额是五百元。"
    assert words.clean("The gap is 5 mm wide.") == "The gap is 5 mm wide."
    assert words.clean("Run the ER 1.2 build.") == "Run the ER 1.2 build."
    assert words.clean("use a fastener, e.g. the M3 screw.") == "Use a fastener, e.g. the M3 screw."
    # words that are also correction markers, used as ordinary words
    assert words.clean("I need to scratch that itch.") == "I need to scratch that itch."
    assert words.clean("Please delete that file.") == "Please delete that file."
    assert words.clean("I can't come, sorry, I'm busy.") == "I can't come, sorry, I'm busy."


def test_correction_at_the_start_of_a_sentence():
    assert words.clean("It works. No wait, it doesn't.") == "It doesn't."
    assert words.clean("Send the report at three. No wait, at four.") == "Send the report at four."


def test_my_words_keep_their_own_capitals_at_the_start():
    assert words.process("e green survey is open.", MY_WORDS) == "eGreen survey is open."


def test_indonesian_cleanup():
    clean = lambda said: words.clean(said, language="id-ID")
    assert clean("Kirim jam tiga, eh salah, jam empat.") == "Kirim jam empat."
    assert clean("Ehm, tolong buka filenya.") == "Tolong buka filenya."
    assert clean("Eh, tolong buka filenya.") == "Tolong buka filenya."
    # a doubled word is a plural in Indonesian, not a stutter
    assert clean("anak anak bermain") == "Anak anak bermain"
    # "serius" is a word here, so it is never taken for a misheard ZIRIS
    assert words.process("dia serius sekali", MY_WORDS, language="id-ID") == "Dia serius sekali"
    assert words.clean("Ehm, tolong buka filenya.", language="ms-MY") == "Tolong buka filenya."


def test_german_words_that_look_like_english_fillers_stay():
    assert words.clean("Er hat um drei angerufen.", language="de-DE") == "Er hat um drei angerufen."


def test_chinese_corrections_simplified_and_traditional():
    clean = lambda said, language="zh-CN": words.clean(said, language=language)
    assert clean("明天下午三点开会，不对，明天下午四点开会。") == "明天下午四点开会。"
    assert clean("发给张三，不对，李四。") == "发给李四。"
    assert clean("我明天去上海，不对，后天去。") == "我后天去上海。"
    assert clean("今天发货。删掉这句。周五发货。") == "周五发货。"
    assert clean("發給張三，不對，李四。", "zh-TW") == "發給李四。"
    assert clean("發給張三，說錯了，李四。", "zh-TW") == "發給李四。"


def test_terms_and_snippets_in_any_script():
    assert words.apply_terms("台积电的订单", ["台積電"]) == "台積電的订单"
    assert words.apply_terms("call Mr Muller today", ["Müller"]) == "call Mr Müller today"
    assert words.apply_snippets("请发到我的邮箱", [("我的邮箱", "juwen@example.com")]) == "请发到juwen@example.com"


def test_language_is_read_from_the_text_when_unknown():
    for said, language in (
            ("Please send the report to the team before Friday.", "en"),
            ("Tolong kirim laporan itu ke tim sebelum hari Jumat.", "id"),
            ("Saya mau meeting dengan vendor besok, tolong siapkan laporan.", "id"),
            ("明天下午三点开会。", "zh"),
            ("明日の会議は三時からです。", "ja"),
            ("내일 회의는 세 시에 시작합니다.", "ko"),
            ("พรุ่งนี้มีประชุมตอนบ่ายสามโมง", "th"),
            ("Tôi sẽ gửi báo cáo vào ngày mai.", "vi"),
            ("Bitte schicken Sie den Bericht vor Freitag an das Team.", "de"),
            ("Por favor envía el informe al equipo antes del viernes.", "es"),
            ("Merci d'envoyer le rapport à l'équipe avant vendredi.", "fr"),
            ("OK.", "xx"), ("SRM PLM BOM", "xx")):
        assert words._guess(said) == language, said
    # "auto" uses it: Indonesian rules for Indonesian text, English rules for English text
    assert words.clean("Kirim jam tiga, eh salah, jam empat, tolong kirim hari ini.", language="auto") == (
        "Kirim jam empat, tolong kirim hari ini.")
    assert words.clean("Um, send it to the team, no wait, to the vendor.", language="auto") == (
        "Send it to the vendor.")


def test_made_up_word_that_sounds_like_my_word(monkeypatch):
    # the real miss from the 1.2.0 benchmark: "the ZIRIS" was written as "Bizaira's"
    monkeypatch.setattr(words, "_vocabulary", lambda: ENGLISH)
    said = "Type Hello team, Bizaira's power test passed."
    assert words.process(said, BENCH_WORDS) == "Type Hello team, ZIRIS power test passed."


def test_real_words_and_names_are_never_replaced_by_sound(monkeypatch):
    monkeypatch.setattr(words, "_vocabulary", lambda: ENGLISH)
    for said in ("Sirius is the brightest star.", "Cyrus called about the series.", "The series starts on Friday.",
                 "Suarez scored twice.", "Soros funded the project.", "Zara's new store opened.",
                 "Zahira's report is late.", "Nasir's team is ready."):
        assert words.process(said, BENCH_WORDS) == said, said


def test_sound_rule_is_off_without_a_dictionary_or_in_languages_it_cannot_check(monkeypatch):
    said = "Type Hello team, Bizaira's power test passed."
    monkeypatch.setattr(words, "_vocabulary", lambda: None)
    assert words.process(said, BENCH_WORDS) == said
    monkeypatch.setattr(words, "_vocabulary", lambda: ENGLISH)
    assert words.process(said, BENCH_WORDS, language="id-ID") == said


def test_sound_rule_with_the_dictionary_that_ships_with_the_voice():
    import pytest
    pytest.importorskip("misaki")
    words._vocabulary.cache_clear()
    if words._vocabulary() is None:
        pytest.skip("misaki is installed without its English word list")
    assert words.process("Type Hello team, Bizaira's power test passed.", BENCH_WORDS) == (
        "Type Hello team, ZIRIS power test passed.")
    for said in ("Sirius is the brightest star.", "Cyrus called about the series.", "Suarez scored twice.",
                 "Soros funded the project.", "Zara's new store opened.", "Zahira's report is late.",
                 "Nasir's team is ready.", "We saw Osiris and Iris at the museum.", "The bizarre bazaar was busy."):
        assert words.process(said, BENCH_WORDS) == said, said


def test_edit_commands_in_other_languages():
    for said in ("terjemahkan ke bahasa Inggris", "Tolong ringkaskan ini.", "buat lebih formal",
                 "bikin jadi poin-poin", "翻譯成英文", "把这段翻译成英文", "把語氣改得正式一點", "请帮我润色一下",
                 "これを英語に翻訳してください", "もっと短くして", "영어로 번역해 주세요", "더 짧게 해줘",
                 "Mach das kürzer.", "Übersetze ins Englische.", "Resume esto en dos frases.", "Traduis en anglais."):
        assert words.is_edit_command(said), said
    for said in ("Resume the test at five.", "Mach 2 is twice the speed of sound.", "Buat saya, ini penting.",
                 "Singkat cerita, kami pulang.", "把文件发给我", "改天再说吧", "明天下午三点开会",
                 "Saya akan datang besok.", "会議は三時からです", "내일 회의가 있습니다"):
        assert not words.is_edit_command(said), said
