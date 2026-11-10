"""Chinese script conversion (keyit_script): Simplified <-> Traditional (Taiwan), from the OpenCC tables."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import keyit_script as script  # noqa: E402


def test_tables_are_shipped_with_their_licence():
    for name in ("STCharacters.txt", "STPhrases.txt", "TWVariants.txt"):
        assert (script.TABLES / name).stat().st_size > 100, name
    assert (script.TABLES / "README.md").exists()
    assert "Apache License" in (script.TABLES / "LICENSE").read_text(encoding="utf-8")


def test_simplified_to_traditional_taiwan():
    assert script.to_traditional("帮我打开记事本。") == "幫我打開記事本。"
    assert script.to_traditional("这里的系统有关系") == "這裡的系統有關係"
    assert script.to_traditional("为什么") == "為什麼"
    assert script.to_traditional("请把这个文件发给我") == "請把這個文件發給我"
    assert script.to_traditional("我看着他") == "我看著他"


def test_phrases_decide_characters_with_two_traditional_forms():
    # 发 is 發 (send) or 髮 (hair); 后 is 後 (after) or 后 (queen); 面 is 面 (side) or 麵 (noodles)
    assert script.to_traditional("头发和发现") == "頭髮和發現"
    assert script.to_traditional("皇后在后面") == "皇后在後面"
    assert script.to_traditional("下面吃面条") == "下面吃麵條"
    assert script.to_traditional("干净的干部") == "乾淨的幹部"


def test_taiwan_uses_the_everyday_tai():
    assert script.to_traditional("台湾的平台") == "台灣的平台"
    assert script.to_traditional("台风来了") == "颱風來了"


def test_traditional_to_simplified():
    assert script.to_simplified("幫我打開記事本。") == "帮我打开记事本。"
    assert script.to_simplified("頭髮和發現") == "头发和发现"
    assert script.to_simplified("這裡的系統有關係") == "这里的系统有关系"
    assert script.to_simplified("為什麼") == "为什么"
    assert script.to_simplified("乾淨") == "干净"
    assert script.to_simplified("臺灣和台灣") == "台湾和台湾"


def test_text_already_in_the_target_script_is_left_alone():
    simplified = "著名的乾坤，覆盖了整个系统，然后发现了问题。"
    assert script.to_simplified(simplified) == simplified
    traditional = "這裡的系統有關係，然後發現了問題。"
    assert script.to_traditional(traditional) == traditional


def test_round_trip_of_everyday_sentences():
    for sentence in ("帮我打开记事本。", "明天下午三点开会，请准时参加。", "这个零件的测试报告已经发给供应商了。"):
        assert script.to_simplified(script.to_traditional(sentence)) == sentence


def test_other_text_is_untouched():
    assert script.to_traditional("Open the SRM survey.") == "Open the SRM survey."
    assert script.to_traditional("") == ""
    assert script.to_traditional("打开SRM系统") == "打開SRM系統"


def test_fold_keeps_the_length():
    assert script.fold("說錯了") == "说错了"
    assert script.fold("這裡不對") == "这里不对"
    assert script.fold("著名") == "著名"
    for text in ("刪掉這句", "打開SRM系統", "hello", "頭髮和發現"):
        assert len(script.fold(text)) == len(text)


def test_convert_follows_the_language_setting():
    assert script.convert("这里的系统", "zh-TW") == "這裡的系統"
    assert script.convert("這裡的系統", "zh-CN") == "这里的系统"
    for language in ("zh", "ja-JP", "auto", "en-US", "", None):
        assert script.convert("这里的系統", language) == "这里的系統"


def test_cantonese_is_typed_in_traditional_characters():
    # measured on FLEURS: the speech model writes Cantonese in Simplified (33.3% of characters differ from the
    # Hong Kong reference); after conversion only its real mishearings are left (6.0%)
    assert script.convert("警方说尸体看起来已摆在那里一天左右", "yue") == "警方說屍體看起來已擺在那裡一天左右"
    assert script.convert("佢哋喺香港电台做嘢", "yue") == "佢哋喺香港電台做嘢"


def test_script_names_the_characters_a_setting_types():
    assert script.script("zh-TW") == script.script("zh_HK") == script.script("yue") == "traditional"
    assert script.script("zh-CN") == script.script("zh-Hans") == "simplified"
    for language in ("zh", "ja-JP", "auto", "en-US", "", None):
        assert script.script(language) == ""


def test_missing_tables_change_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(script, "TABLES", tmp_path)
    script._tables.cache_clear()
    try:
        assert script.to_traditional("这里的系统") == "这里的系统"
        assert script.to_simplified("這裡的系統") == "這裡的系統"
        assert script.fold("說錯了") == "說錯了"
    finally:
        script._tables.cache_clear()
