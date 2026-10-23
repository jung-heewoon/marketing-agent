import httpx

from app.llm import parse_model_json
from scripts.eval_drafts import EXAMPLE_VIDEOS
from app.youtube import build_excerpt, parse_feed, parse_video_id


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
 <title>외롭여</title>
 <entry>
  <yt:videoId>k_0c0m0Hk5k</yt:videoId>
  <yt:channelId>UCxlc0Ky0aWoD8uI09ad44Tg</yt:channelId>
  <title>서운함 많은 여자들이 꼭 알아야 할 것</title>
  <published>2026-10-18T08:30:08+00:00</published>
  <author><name>외롭여</name></author>
  <media:group>
   <media:title>서운함 많은 여자들이 꼭 알아야 할 것</media:title>
   <media:thumbnail url="https://i.ytimg.com/vi/k_0c0m0Hk5k/hqdefault.jpg"/>
   <media:description>연락이 없으면 하루가 망가진다.</media:description>
  </media:group>
 </entry>
</feed>
"""


def test_eval_uses_the_assignment_examples():
    assert EXAMPLE_VIDEOS == (
        "akn-AvCeHBI",
        "JjY09DSdzx0",
        "DvxMfC_KjX0",
        "3K82-PjUeP0",
        "WxXINTXQSd4",
    )


def test_parse_video_id():
    assert parse_video_id("k_0c0m0Hk5k") == "k_0c0m0Hk5k"
    assert parse_video_id("https://youtu.be/akn-AvCeHBI") == "akn-AvCeHBI"
    assert parse_video_id("https://www.youtube.com/watch?v=JjY09DSdzx0&t=3") == "JjY09DSdzx0"
    assert parse_video_id("https://www.youtube.com/shorts/DvxMfC_KjX0") == "DvxMfC_KjX0"
    assert parse_video_id("그냥 글") is None


def test_channel_feed_retries_a_temporary_youtube_error(monkeypatch):
    from dataclasses import replace

    from app.config import Channel, load_settings
    from app.youtube import fetch_channels

    calls = {"n": 0}

    class Response:
        def __init__(self, status: int, text: str = ""):
            self.status_code = status
            self.text = text

        def raise_for_status(self):
            if self.status_code >= 400:
                request = httpx.Request("GET", "https://www.youtube.com/feeds/videos.xml")
                response = httpx.Response(self.status_code, request=request)
                raise httpx.HTTPStatusError("fail", request=request, response=response)

    class Client:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            del url
            calls["n"] += 1
            if calls["n"] < 3:
                return Response(404)
            return Response(200, FEED)

    monkeypatch.setattr("app.youtube.httpx.Client", Client)
    monkeypatch.setattr("app.youtube.time.sleep", lambda seconds: None)
    settings = replace(
        load_settings(),
        channels=[Channel("UCxlc0Ky0aWoD8uI09ad44Tg", "외롭여")],
    )
    videos, warnings = fetch_channels(settings)
    assert calls["n"] == 3
    assert warnings == []
    assert videos[0]["video_id"] == "k_0c0m0Hk5k"


def test_parse_feed():
    videos = parse_feed(FEED, "대체", 5)
    assert len(videos) == 1
    assert videos[0]["video_id"] == "k_0c0m0Hk5k"
    assert videos[0]["channel_name"] == "외롭여"
    assert videos[0]["channel_id"] == "UCxlc0Ky0aWoD8uI09ad44Tg"
    assert "연락이 없으면" in videos[0]["description"]
    assert videos[0]["thumbnail"].endswith("hqdefault.jpg")


def test_excerpt_keeps_relationship_lines():
    filler = "오늘 날씨가 흐리다. " * 80
    transcript = filler + "재회하고 싶은데 연락이 두렵다. " + filler
    excerpt = build_excerpt(transcript, limit=500)
    assert "재회하고 싶은데" in excerpt
    assert len(excerpt) <= 500


def test_parse_model_json():
    raw = """<think>생각</think>
```json
{"concern": "연락이 두렵다", "comment": "댓글", "evidence": "재회"}
```"""
    parsed = parse_model_json(raw)
    assert parsed["concern"] == "연락이 두렵다"
    assert parsed["comment"] == "댓글"
    korean = '{"고민": "서운함", "댓글": "운명원", "근거": "서운"}'
    assert parse_model_json(korean)["concern"] == "서운함"
    batch = '{"topic": "재회", "evidence": "연락", "concern": "기다림", "comments": ["하나", "둘", "하나"]}'
    parsed_batch = parse_model_json(batch)
    assert parsed_batch["comments"] == ["하나", "둘"]
    assert parsed_batch["comment"] == "하나"
