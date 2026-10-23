import random

from app.voice import accept_comment, pick_guidance, sample_diverse


def test_accepts_casual_comment_and_rejects_noise():
    kept = accept_comment("근데 진짜 남자심리는 알다가도 모르겠다..ㅠㅠ 맨날 차이기만 했던 1인")
    assert kept is not None
    assert kept["tag"] == "혼잣말"
    assert accept_comment("씨발 진짜 열받네요 이게 뭐야") is None
    assert accept_comment("진짜 좋던데 https://example.com 한번 봐요") is None
    assert accept_comment("운명원 연애사주 진짜 궁금하네요 저도요") is None
    assert accept_comment("좋아요") is None
    assert accept_comment("오늘 날씨가 흐리고 바람이 많이 불었습니다 산책하기 좋아요") is None
    assert accept_comment("본질은 내가 진짜 남자가 되는 것입니다. 이런 말은 스킬이 아닙니다.") is None


def test_sample_diverse_tags():
    items = [
        {"text": "근데 진짜 내 얘기네요", "tag": "혼잣말"},
        {"text": "저도 맨날 그러더라고요", "tag": "친구에게 말하듯"},
        {"text": "기다리기만 하는 사람이네요", "tag": "한숨"},
        {"text": "아 진짜 또 그 자리네요", "tag": "혼잣말"},
    ]
    picked = sample_diverse(items, 3, random.Random(1))
    assert len(picked) == 3
    assert len({item["tag"] for item in picked}) == 3


def test_roll_weights_casual_voice():
    items = [
        {"text": "근데 진짜요", "tag": "혼잣말"},
        {"text": "그러더라고요", "tag": "친구에게 말하듯"},
        {"text": "그렇네요", "tag": "한숨"},
    ]
    casual = pick_guidance(items, 0.2, random.Random(0))
    also = pick_guidance(items, 0.9, random.Random(0))
    empty = pick_guidance([], 0.1, random.Random(0))
    assert casual["kind"] == "casual"
    assert also["kind"] == "casual"
    assert len(casual["samples"]) == 3
    assert empty["kind"] == "plain"
