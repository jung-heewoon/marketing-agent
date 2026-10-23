import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import load_settings


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKETING_AGENT_DB", str(tmp_path / "agent.db"))
    from app import main as main_module

    with TestClient(main_module.app) as test_client:
        yield test_client


def test_dashboard_and_empty_log(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "영상 불러오기" in page.text
    assert "댓글 달기" in page.text
    assert client.get("/api/videos").json()["videos"] == []
    assert client.get("/api/posts").json()["posts"] == []
    health = client.get("/api/health")
    assert health.status_code == 200
    assert "profiles" in health.json()


def test_rejects_bad_link_and_short_comment(client):
    bad = client.post("/api/videos/resolve", json={"url": "안녕하세요"})
    assert bad.status_code == 400
    settings = load_settings()
    channel = settings.channels[0]
    db.upsert_video(
        {
            "video_id": "k_0c0m0Hk5k",
            "channel_id": channel.id,
            "channel_name": channel.name,
            "title": "서운함 많은 여자들이 꼭 알아야 할 것",
            "description": "연락이 없으면 하루가 망가진다.",
            "published_at": "2026-10-18T08:30:08+00:00",
            "thumbnail": "",
            "url": "https://www.youtube.com/watch?v=k_0c0m0Hk5k",
            "fetched_at": "2026-10-22T00:00:00+09:00",
        }
    )
    db.save_draft(
        {
            "video_id": "k_0c0m0Hk5k",
            "concern": "서운함",
            "comment": "초안",
            "evidence": "서운함 많은 여자",
            "source_text": "서운함 많은 여자들이 꼭 알아야 할 것",
            "source_kind": "title",
            "warnings": "",
        }
    )
    rejected = client.post(
        "/api/posts",
        json={"video_id": "k_0c0m0Hk5k", "profile_id": "youtube1", "comment": "너무 짧다"},
    )
    assert rejected.status_code == 400
    assert "자" in rejected.json()["detail"]
    blocked = client.post("/api/posts", json={"video_id": "missingvideo", "comment": "없음", "profile_id": "youtube1"})
    assert blocked.status_code == 404


def test_daily_cap_blocks_before_browser(client):
    settings = load_settings()
    channel = settings.channels[0]
    db.upsert_video(
        {
            "video_id": "abcdefghijk",
            "channel_id": channel.id,
            "channel_name": channel.name,
            "title": "테스트",
            "description": "서운함 많은 여자",
            "published_at": "2026-10-22T00:00:00+00:00",
            "thumbnail": "",
            "url": "https://www.youtube.com/watch?v=abcdefghijk",
            "fetched_at": "2026-10-22T00:00:00+09:00",
        }
    )
    db.save_draft(
        {
            "video_id": "abcdefghijk",
            "concern": "고민",
            "comment": "초안",
            "evidence": "서운함 많은 여자",
            "source_text": "서운함 많은 여자들이 꼭 알아야 할 것",
            "source_kind": "title",
            "warnings": "",
        }
    )
    for index in range(settings.daily_max_comments):
        db.add_post(
            {
                "video_id": f"other{index:05d}",
                "profile_id": "youtube1",
                "comment": "이미 단 댓글",
                "status": "success",
                "reason": "",
                "screenshot": "",
            }
        )
    response = client.post(
        "/api/posts",
        json={
            "video_id": "abcdefghijk",
            "profile_id": "youtube1",
            "comment": "서운함 많은 여자라 답장 하나에 멘탈이 나간다. 운명원 연애사주나 보고 싶다 오늘.",
        },
    )
    assert response.status_code == 400
    assert "하루 게시 상한" in response.json()["detail"]
    other = client.post(
        "/api/posts",
        json={
            "video_id": "abcdefghijk",
            "profile_id": "youtube2",
            "comment": "서운함 많은 여자라 답장 하나에 멘탈이 나간다. 운명원 연애사주나 보고 싶다 오늘.",
        },
    )
    assert other.status_code == 400
    assert "하루 게시 상한" in other.json()["detail"]


def test_log_keeps_success_and_failure_in_time_order(client):
    settings = load_settings()
    channel = settings.channels[0]
    db.upsert_video(
        {
            "video_id": "newervideo1",
            "channel_id": channel.id,
            "channel_name": channel.name,
            "title": "최신",
            "description": "",
            "published_at": "2026-10-23T00:00:00+00:00",
            "thumbnail": "",
            "url": "https://www.youtube.com/watch?v=newervideo1",
            "fetched_at": "2026-10-23T00:00:00+09:00",
        }
    )
    db.upsert_video(
        {
            "video_id": "oldervideo1",
            "channel_id": channel.id,
            "channel_name": channel.name,
            "title": "이전",
            "description": "",
            "published_at": "2026-10-20T00:00:00+00:00",
            "thumbnail": "",
            "url": "https://www.youtube.com/watch?v=oldervideo1",
            "fetched_at": "2026-10-23T00:00:00+09:00",
        }
    )
    db.add_post(
        {
            "video_id": "oldervideo1",
            "profile_id": "youtube1",
            "comment": "보고 싶다",
            "status": "success",
            "reason": "",
            "screenshot": "",
        }
    )
    db.add_post(
        {
            "video_id": "oldervideo1",
            "profile_id": "youtube1",
            "comment": "실패한 시도",
            "status": "failed",
            "reason": "입력 실패",
            "screenshot": "",
        }
    )
    data = client.get("/api/posts").json()
    assert [post["status"] for post in data["posts"]] == ["failed", "success"]
    failed, posted = data["posts"]
    assert failed["created_label"] and posted["created_label"]
    assert posted["sentence"] == (
        f"유튜브1의 프로필이 {channel.name}의 2번 영상 「이전」에 댓글을 보고 싶다로 남겼습니다."
    )
    assert "남기지 못했습니다" in failed["sentence"]
    assert "입력 실패" in failed["sentence"]
    assert "실패한 시도" in failed["sentence"]
    assert data["today"] == 1
    assert data["posted_video_ids"] == ["oldervideo1"]


def test_one_success_blocks_every_profile(client):
    settings = load_settings()
    channel = settings.channels[0]
    db.upsert_video(
        {
            "video_id": "sharedvideo1",
            "channel_id": channel.id,
            "channel_name": channel.name,
            "title": "서운함 많은 여자",
            "description": "서운함 많은 여자",
            "published_at": "2026-10-22T00:00:00+00:00",
            "thumbnail": "",
            "url": "https://www.youtube.com/watch?v=sharedvideo1",
            "fetched_at": "2026-10-22T00:00:00+09:00",
        }
    )
    db.save_draft(
        {
            "video_id": "sharedvideo1",
            "concern": "고민",
            "comment": "초안",
            "evidence": "서운함 많은 여자",
            "source_text": "서운함 많은 여자들이 꼭 알아야 할 것",
            "source_kind": "title",
            "warnings": "",
        }
    )
    db.add_post(
        {
            "video_id": "sharedvideo1",
            "profile_id": "youtube1",
            "comment": "이미 단 댓글",
            "status": "success",
            "reason": "",
            "screenshot": "",
        }
    )
    comment = "서운함 많은 여자라 답장 하나에 멘탈이 나간다. 운명원 연애사주나 보고 싶다 오늘."
    again = client.post(
        "/api/posts",
        json={"video_id": "sharedvideo1", "profile_id": "youtube2", "comment": comment},
    )
    assert again.status_code == 400
    assert "이미 댓글" in again.json()["detail"]
    assert "영상당 1개" in again.json()["detail"]
