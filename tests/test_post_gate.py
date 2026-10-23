import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import load_settings
from app.llm import seen_material
from app.rules import Reject, assert_can_post, expresses_curiosity, find_evidence, validate_comment
from app.youtube import VideoContext


SOURCE = "서운함 많은 여자들이 꼭 알아야 할 것. 답장 하나에 멘탈이 나간다."
GOOD = "서운함 많은 여자라 답장 하나에 멘탈이 나간다. 분석은 그만하고 운명원 연애사주나 보고 싶다."


def _fit(length: int) -> str:
    core = "서운함여자답장멘탈운명원연애사주보고싶다"
    if length <= len(core):
        return core[:length]
    return core + ("요" * (length - len(core)))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKETING_AGENT_DB", str(tmp_path / "agent.db"))
    from app import main as main_module

    with TestClient(main_module.app) as test_client:
        yield test_client


def _video(video_id: str, channel_id: str, title: str = "서운함 많은 여자") -> None:
    db.upsert_video(
        {
            "video_id": video_id,
            "channel_id": channel_id,
            "channel_name": "테스트",
            "title": title,
            "description": SOURCE,
            "published_at": "2026-10-22T00:00:00+00:00",
            "thumbnail": "",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "fetched_at": "2026-10-22T00:00:00+09:00",
        }
    )


def test_length_bounds_and_placeholder_counts():
    settings = load_settings()
    evidence = "서운함 많은 여자"
    for length, failed in ((39, True), (40, False), (120, False), (121, True)):
        errors = validate_comment(_fit(length), SOURCE, evidence, settings)
        assert any(error.startswith("댓글은") for error in errors) is failed


def test_negated_curiosity_fails_and_plain_curiosity_passes():
    settings = load_settings()
    denied = "서운함 많은 여자라 답장 하나에 멘탈이 나간다. 운명원 연애사주가 궁금하지는 않네요."
    thought = "서운함 많은 여자라 답장 하나에 멘탈이 나간다. 운명원 연애사주를 보고 싶다는 생각은 안 들었어요."
    allowed = "서운함 많은 여자라 답장 하나에 멘탈이 나간다. 운명원 연애사주에서는 뭐라고 할지 궁금하네요."
    assert expresses_curiosity(denied) is False
    assert expresses_curiosity(thought) is False
    assert any("궁금" in error for error in validate_comment(denied, SOURCE, "서운함 많은", settings))
    assert any("궁금" in error for error in validate_comment(thought, SOURCE, "서운함 많은", settings))
    evidence = find_evidence(allowed, SOURCE)
    assert validate_comment(allowed, SOURCE, evidence, settings) == []


def test_evidence_ignores_sentences_the_model_did_not_see():
    context = VideoContext(
        transcript="",
        comments=["재회하고 싶은데 답장이 늦어서 불안하다"],
        description="서운함 많은 여자",
        source_text="재회하고 싶은데 답장이 늦어서 불안하다. 서운함 많은 여자",
        source_kind="transcript",
        excerpt="서운함 많은 여자들이 꼭 알아야 할 것",
        summary="",
    )
    seen = seen_material({"title": "서운함"}, context)
    comment = "재회하고 싶은데 답장이 늦으면 운명원 연애사주 보고 싶다"
    assert "재회" not in seen
    assert find_evidence(comment, seen) == ""
    assert find_evidence(comment, context.source_text)


def test_unchanged_held_draft_and_edited_review_are_rejected(client):
    settings = load_settings()
    channel = settings.channels[0].id
    _video("heldvideo01", channel)
    db.save_draft(
        {
            "video_id": "heldvideo01",
            "concern": "",
            "comment": GOOD,
            "evidence": "서운함 많은",
            "source_text": SOURCE,
            "source_kind": "transcript",
            "warnings": "게시 불가. 규칙에 걸렸습니다.",
        }
    )
    same = client.post("/api/posts", json={"video_id": "heldvideo01", "profile_id": "youtube1", "comment": GOOD})
    assert same.status_code == 400
    assert "게시할 수 없습니다" in same.json()["detail"]
    review = "서운함 많은 여자라 답장 하나에 멘탈이 나간다. 내가 운명원 직접 봤는데 잘 맞았다."
    edited = client.post("/api/posts", json={"video_id": "heldvideo01", "profile_id": "youtube1", "comment": review})
    assert edited.status_code == 400
    assert "후기" in edited.json()["detail"]


def test_title_only_edit_without_evidence_is_rejected(client):
    settings = load_settings()
    channel = settings.channels[0].id
    _video("titlevideo01", channel, title="짧은 제목")
    db.save_draft(
        {
            "video_id": "titlevideo01",
            "concern": "",
            "comment": "저장된 초안",
            "evidence": "",
            "source_text": "짧은 제목",
            "source_kind": "title",
            "warnings": "검토 필요. 자막이 없어 댓글을 확인하고 고친 뒤 게시하세요.",
        }
    )
    edited = client.post(
        "/api/posts",
        json={"video_id": "titlevideo01", "profile_id": "youtube1", "comment": "저장된 초안"},
    )
    assert edited.status_code == 400
    assert "게시할 수 없습니다" in edited.json()["detail"]


def test_disallowed_channel_and_duplicate_video_are_rejected(client):
    settings = load_settings()
    _video("foreignvid1", "UCnot-allowed-channel-0000")
    db.save_draft(
        {
            "video_id": "foreignvid1",
            "concern": "",
            "comment": GOOD,
            "evidence": "서운함 많은",
            "source_text": SOURCE,
            "source_kind": "transcript",
            "warnings": "",
        }
    )
    foreign = client.post("/api/posts", json={"video_id": "foreignvid1", "profile_id": "youtube1", "comment": GOOD})
    assert foreign.status_code == 400
    assert "허용된 채널" in foreign.json()["detail"]

    channel = settings.channels[0].id
    _video("postedvid01", channel)
    db.save_draft(
        {
            "video_id": "postedvid01",
            "concern": "",
            "comment": "다른 초안",
            "evidence": "서운함 많은",
            "source_text": SOURCE,
            "source_kind": "transcript",
            "warnings": "",
        }
    )
    db.add_post(
        {
            "video_id": "postedvid01",
            "profile_id": "youtube1",
            "comment": GOOD,
            "status": "success",
            "reason": "",
            "screenshot": "",
        }
    )
    again = client.post("/api/posts", json={"video_id": "postedvid01", "profile_id": "youtube1", "comment": GOOD})
    assert again.status_code == 400
    assert "이미 댓글" in again.json()["detail"]


def test_style_and_missing_scene_do_not_block_the_gate():
    settings = load_settings()
    channel = settings.channels[0].id
    curious_gap = "재회하고 싶은데 답장이 늦는 하루가 길다. 운명원 연애사주나 한번 보고 싶다."
    assert_can_post(
        video_id="abcdefghijk",
        channel_id=channel,
        comment=curious_gap,
        source="짧은 제목",
        evidence="",
        settings=settings,
        success_today=0,
        already_posted=False,
    )
    denied = "서운함 많은 여자라 답장 하나에 멘탈이 나간다. 운명원 연애사주가 궁금하지는 않네요."
    try:
        assert_can_post(
            video_id="abcdefghijk",
            channel_id=channel,
            comment=denied,
            source=SOURCE,
            evidence="서운함 많은",
            settings=settings,
            success_today=0,
            already_posted=False,
        )
        raise AssertionError("negated curiosity should fail")
    except Reject as exc:
        assert "궁금" in exc.message
    person = "운명원은 남자심리 알기보다 그냥 인연이면 만나는 거라고 믿는 차이기만 했던 사람입니다."
    subject = "운명원은 내 하루의 전부가 아닌, 내 삶의 중심을 지키는 여자라는 말에 마음이 찡해용. 오늘도 그래요."
    for blocked in (person, subject):
        try:
            assert_can_post(
                video_id="abcdefghijk",
                channel_id=channel,
                comment=blocked,
                source=SOURCE,
                evidence="서운함 많은",
                settings=settings,
                success_today=0,
                already_posted=False,
            )
            raise AssertionError("brand used as the sentence subject should fail")
        except Reject as exc:
            assert "사람" in exc.message


def test_assert_can_post_still_checks_channel():
    settings = load_settings()
    try:
        assert_can_post(
            video_id="abcdefghijk",
            channel_id="UCnot-allowed-channel-0000",
            comment=GOOD,
            source=SOURCE,
            evidence="서운함 많은",
            settings=settings,
            success_today=0,
            already_posted=False,
        )
        raise AssertionError("blocked channel should fail")
    except Reject as exc:
        assert "허용된 채널" in exc.message
