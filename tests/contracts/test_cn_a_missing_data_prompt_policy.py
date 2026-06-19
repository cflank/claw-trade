from pathlib import Path


def test_cn_a_prompts_skip_missing_data_instead_of_exposing_it() -> None:
    forbidden_missing_data_phrases = (
        "无法验证",
        "无法确认",
        "未取得",
        "资料缺口",
        "数据缺口",
        "数据限制",
        "覆盖限制",
        "覆盖不足",
        "限制说明",
    )
    prompt_paths = sorted(Path("agents").glob("*/prompts/CN_A.md"))

    assert prompt_paths
    for path in prompt_paths:
        text = path.read_text(encoding="utf-8")
        for phrase in forbidden_missing_data_phrases:
            assert phrase not in text, f"{path} contains {phrase!r}"
