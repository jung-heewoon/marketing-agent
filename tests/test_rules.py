from datetime import datetime, timedelta, timezone

from app.config import load_settings
from app.rules import (
    assert_can_post,
    comment_length,
    hard_errors,
    is_rising,
    post_sentence,
    style_notes,
    validate_comment,
    video_allowed,
    views_per_hour,
)
from app.rules import Reject


def _settings():
    return load_settings()


SOURCE = "서운함 많은 여자들이 꼭 알아야 할 것. 연락이 없으면 하루가 망가진다."
EVIDENCE = "서운함 많은 여자"
GOOD = "서운함 많은 여자라 답장 하나에 멘탈이 나간다. 분석은 그만하고 운명원 연애사주나 보고 싶다."


def test_good_comment_passes():
    settings = _settings()
    assert settings.min_chars <= comment_length(GOOD) <= settings.max_chars
    assert hard_errors(GOOD, settings) == []
    assert style_notes(GOOD, settings) == []
    assert validate_comment(GOOD, SOURCE, EVIDENCE, settings) == []


def test_style_notes_do_not_block_posting():
    settings = _settings()
    channel = settings.channels[0].id
    awkward = "환승이별 용서가 어렵다는 말에 공감한다. 연애사주 운명원 소문이 궁금하다 정말 오늘도."
    assert style_notes(awkward, settings)
    assert_can_post(
        video_id="abcdefghijk",
        channel_id=channel,
        comment=GOOD,
        source="",
        evidence="",
        settings=settings,
        success_today=0,
        already_posted=False,
    )


def test_length_brand_link_hashtag_and_fake_review():
    settings = _settings()
    assert any("자여야" in item for item in validate_comment("운명원 너무 짧다", SOURCE, EVIDENCE, settings))
    long_comment = "서운함 많은 여자라 " + ("정말 " * 40) + "운명원 연애사주가 보고 싶다."
    assert any("자여야" in item for item in validate_comment(long_comment, SOURCE, EVIDENCE, settings))
    missing = GOOD.replace("운명원", "다른곳")
    assert any("운명원" in item for item in validate_comment(missing, SOURCE, EVIDENCE, settings))
    linked = GOOD.replace("보고 싶다.", "https://example.com 가보자.")
    assert any("링크" in item for item in validate_comment(linked, SOURCE, EVIDENCE, settings))
    tagged = GOOD.replace("보고 싶다.", "#연애사주 보고 싶다.")
    assert any("해시태그" in item for item in validate_comment(tagged, SOURCE, EVIDENCE, settings))
    fake = "서운함 많은 여자였는데 운명원에서 봤는데 진짜 맞았다. 각자 운명이 있는 듯했다."
    assert any("후기" in item for item in validate_comment(fake, SOURCE, EVIDENCE, settings))


def test_brand_must_make_the_reader_curious():
    settings = _settings()
    comment = "서운함 많은 여자라 연락 하나에 통역하는 기분이에요. 마음 분석은 지쳤고 운명원 연애사주가 잘 본다던데 나도 보고 싶네요."
    assert validate_comment(comment, SOURCE, EVIDENCE, settings) == []
    tagged = "열심히 준비해도 상대가 판 안 깔면 분위기 식는다, 운명원도 결국 사람의 마음인데 서운함 많은 여자라 더 그래요."
    assert any("쉼표" in item or "보고 싶" in item for item in validate_comment(tagged, SOURCE, EVIDENCE, settings))


def test_hearsay_is_allowed():
    settings = _settings()
    comment = "서운함 많은 여자라 마음 계산이 의미 없다. 운명원 연애사주가 잘 본다던데 나도 보고 싶다."
    assert validate_comment(comment, SOURCE, EVIDENCE, settings) == []


def test_evidence_must_come_from_the_video():
    settings = _settings()
    assert any("근거" in item for item in validate_comment(GOOD, SOURCE, "전혀 없는 문장입니다", settings))
    comment = "연락이 없는 하루가 너무 길다. 운명원 연애사주나 한번 보고 싶다 정말."
    assert any("키워드" in item or "영상 속" in item for item in validate_comment(comment, SOURCE, EVIDENCE, settings))
    natural = "늘 내가 먼저 서운해져서 연애가 힘드네요.. 운명원이 인연사주만 본다는 얘길 들어서 이번 타이밍인지 볼까 봐요"
    assert validate_comment(natural, SOURCE, EVIDENCE, settings) == []
    awkward = "환승이별 용서가 어렵다는 말에 공감한다. 연애사주 운명원 소문이 궁금하다 정말."
    assert any("구어체" in item for item in validate_comment(awkward, "환승이별 용서가 어렵습니다.", "환승이별 용서", settings))


def test_hanja_middle_dot_and_stock_bridge():
    settings = _settings()
    hanja = "서운함 많은 여자라 떠난后才 연락하는 거 보면 속앓이네요. 운명원 연애사주가 이번 타이밍인지 볼까 봐요."
    assert any("한자" in item for item in validate_comment(hanja, SOURCE, EVIDENCE, settings))
    dotted = "서운함 많은 여자라 마음 계산이 의미 없네요. 운명원처럼 연애·인연 사주만 파는 데가 이번 고민이랑 맞을지 모르겠어요."
    assert any("가운뎃점" in item for item in validate_comment(dotted, SOURCE, EVIDENCE, settings))
    paired = "서운함 많은 여자라 마음 계산이 의미 없네요. 운명원처럼 연애랑 인연만 판다던데 이번 고민이랑 맞을지 모르겠어요."
    assert any("가운뎃점" in item for item in validate_comment(paired, SOURCE, EVIDENCE, settings))
    stock = "늘 내가 먼저 서운해져서 연애가 힘드네요.. 운명원 인연사주가 내 짝이나 있는지 보고 싶어요"
    assert any("결말" in item for item in validate_comment(stock, SOURCE, EVIDENCE, settings))
    varied = "서운함 많은 여자라 같은 유형만 만나네요. 운명원이 인연사주만 판다길래 왜 이런 인연만 반복인지 볼까 봐요."
    assert validate_comment(varied, SOURCE, EVIDENCE, settings) == []
    loose = "환승 직후의 행복이 착각일 수 있다는 말에 찔렸다. 재회 여지가 있는지 궁금해 인연사주 쪽으로 고민 중인데 운명원 소문도 보이더라."
    loose_source = "환승 직후의 행복이 착각일 수 있다."
    assert any("운명원" in item for item in validate_comment(loose, loose_source, "환승 직후", settings))
    tied = "환승 직후 행복이 착각일 수도 있다니까 찔리네요. 전 애인이랑 다시 될지 몰라서 운명원처럼 인연사주만 보는 데 재회를 물어볼까 봐요."
    assert validate_comment(tied, loose_source, "환승 직후", settings) == []


def test_post_sentence_names_profile_channel_and_video_number():
    plain = post_sentence("유튜브1", "외롭여", 1, "보고 싶다")
    assert plain == "유튜브1의 프로필이 외롭여의 1번 영상에 댓글을 보고 싶다로 남겼습니다."
    closed = post_sentence("유튜브2", "심리조각", 3, "운명원")
    assert closed == "유튜브2의 프로필이 심리조각의 3번 영상에 댓글을 운명원으로 남겼습니다."


def test_allowlist_and_post_caps():
    settings = _settings()
    channel = settings.channels[0].id
    assert video_allowed("abcdefghijk", channel, settings)
    assert not video_allowed("abcdefghijk", "UCnot-allowed-channel-0000", settings)
    assert_can_post(
        video_id="abcdefghijk",
        channel_id=channel,
        comment=GOOD,
        source=SOURCE,
        evidence=EVIDENCE,
        settings=settings,
        success_today=0,
        already_posted=False,
    )
    try:
        assert_can_post(
            video_id="abcdefghijk",
            channel_id="UCnot-allowed-channel-0000",
            comment=GOOD,
            source=SOURCE,
            evidence=EVIDENCE,
            settings=settings,
            success_today=0,
            already_posted=False,
        )
        raise AssertionError("blocked channel should fail")
    except Reject as exc:
        assert "허용된 채널" in exc.message
    try:
        assert_can_post(
            video_id="abcdefghijk",
            channel_id=channel,
            comment=GOOD,
            source=SOURCE,
            evidence=EVIDENCE,
            settings=settings,
            success_today=0,
            already_posted=True,
        )
        raise AssertionError("duplicate should fail")
    except Reject as exc:
        assert "영상당 1개" in exc.message
    try:
        assert_can_post(
            video_id="abcdefghijk",
            channel_id=channel,
            comment=GOOD,
            source=SOURCE,
            evidence=EVIDENCE,
            settings=settings,
            success_today=settings.daily_max_comments,
            already_posted=False,
        )
        raise AssertionError("daily cap should fail")
    except Reject as exc:
        assert "하루 게시 상한" in exc.message


def test_instruction_echo_fails_and_spoken_ending_passes():
    settings = _settings()
    echoed = (
        "여자친구가 항상 물어봤대요, 이런 연락이 당시에는 너무 부담스러웠다고들 하죠. "
        "근데, 내 짝도 매일 안부 묻는 거 싫어했다던 게 떠올라서요. "
        "운명원 연애사주가 잘 본다던데, 한번 보고 싶다는 말."
    )
    source = "여자친구가 항상 물어봤대요. 이런 연락이 당시에는 너무 부담스러웠다고들 하죠."
    evidence = "여자친구가 항상 물어봤대"
    assert any("설명문" in item for item in validate_comment(echoed, source, evidence, settings))
    spoken = "여자친구가 항상 물어봤대, 그때 연락이 부담이었죠. 나도 그래서 운명원 연애사주 한번 보고 싶어요."
    assert validate_comment(spoken, source, evidence, settings) == []


def test_rising_boundaries():
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    assert is_rising(3000, now - timedelta(hours=10), now)
    assert not is_rising(3000, now - timedelta(hours=40), now)
    assert not is_rising(800, now - timedelta(hours=2), now)
    assert not is_rising(6000, now - timedelta(hours=10), now)
    assert is_rising(1000, now - timedelta(hours=1000 / 150), now)
    assert not is_rising(1000, now - timedelta(hours=7), now)
    assert views_per_hour(1500, now - timedelta(hours=5), now) == 300
