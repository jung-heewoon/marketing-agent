from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from app.config import Settings
from app.db import now_kst
from app.rules import is_rising, views_per_hour

ATOM = "http://www.w3.org/2005/Atom"
YT = "http://www.youtube.com/xml/schemas/2015"
MEDIA = "http://search.yahoo.com/mrss/"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_KEYWORDS = (
    "연애", "이별", "남자", "여자", "마음", "짝", "재회", "썸", "결혼", "심리",
    "집착", "환승", "서운", "연락", "잠수", "고백", "차였", "바람", "궁합", "인연", "운명",
    "후폭풍", "회피", "안정", "불안", "권태", "미련", "결별", "고민", "상처", "기다림",
)
_HOST_CHATTER = re.compile(
    r"(구독|좋아요|알림\s*설정|댓글\s*남겨|라이브|채팅창|사연\s*읽|사연자인|보내주신|"
    r"여러분|시청자분|인사드릴게요|시작해\s*볼게요|말씀드렸|보시면\s*됩니다|오늘\s*영상)"
)


@dataclass
class VideoContext:
    transcript: str
    comments: list[str]
    description: str
    source_text: str
    source_kind: str
    excerpt: str
    summary: str = ""
    feed_description: str = ""
    player_description: str = ""


def parse_video_id(text: str) -> str | None:
    raw = (text or "").strip()
    if _VIDEO_ID.match(raw):
        return raw
    patterns = (
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"[?&]v=([A-Za-z0-9_-]{11})",
        r"/shorts/([A-Za-z0-9_-]{11})",
        r"/embed/([A-Za-z0-9_-]{11})",
        r"/live/([A-Za-z0-9_-]{11})",
    )
    for pattern in patterns:
        found = re.search(pattern, raw)
        if found:
            return found.group(1)
    return None


def parse_feed(xml_text: str, fallback_name: str, limit: int) -> list[dict]:
    root = ET.fromstring(xml_text)
    videos = []
    for entry in root.findall(f"{{{ATOM}}}entry"):
        video_id = (entry.findtext(f"{{{YT}}}videoId") or "").strip()
        if not video_id:
            continue
        author = entry.find(f"{{{ATOM}}}author")
        channel_name = fallback_name
        if author is not None:
            channel_name = author.findtext(f"{{{ATOM}}}name") or fallback_name
        group = entry.find(f"{{{MEDIA}}}group")
        description = ""
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        if group is not None:
            description = group.findtext(f"{{{MEDIA}}}description") or ""
            thumb = group.find(f"{{{MEDIA}}}thumbnail")
            if thumb is not None and thumb.get("url"):
                thumbnail = thumb.get("url") or thumbnail
        videos.append(
            {
                "video_id": video_id,
                "channel_id": (entry.findtext(f"{{{YT}}}channelId") or "").strip(),
                "channel_name": channel_name,
                "title": (entry.findtext(f"{{{ATOM}}}title") or "").strip(),
                "description": description.strip(),
                "published_at": (entry.findtext(f"{{{ATOM}}}published") or "").strip(),
                "thumbnail": thumbnail,
                "url": f"https://www.youtube.com/watch?v={video_id}",
            }
        )
        if len(videos) >= limit:
            break
    return videos


def _read_feed(client: httpx.Client, url: str, tries: int = 3) -> str:
    """유튜브 목록 주소는 같은 요청에도 404와 500을 번갈아 낸다."""
    last: Exception | None = None
    for attempt in range(tries):
        try:
            response = client.get(url)
            response.raise_for_status()
            return response.text
        except Exception as exc:
            last = exc
            if attempt + 1 < tries:
                time.sleep(0.6)
    assert last is not None
    raise last


def fetch_channels(settings: Settings) -> tuple[list[dict], list[str]]:
    videos: list[dict] = []
    warnings: list[str] = []
    headers = {"User-Agent": UA}
    with httpx.Client(headers=headers, timeout=20, follow_redirects=True) as client:
        for channel in settings.channels:
            url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel.id}"
            try:
                text = _read_feed(client, url)
                videos.extend(parse_feed(text, channel.name, settings.videos_per_channel))
            except Exception as exc:
                warnings.append(f"{channel.name} 영상을 불러오지 못했습니다. {exc}")
    videos.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    return videos, warnings


def build_excerpt(transcript: str, limit: int = 1800) -> str:
    text = re.sub(r"\s+", " ", transcript or "").strip()
    if len(text) <= limit:
        return text
    raw_parts = re.split(
        r"(?<=[.!?。])\s+|(?<=다)\s+|(?<=요)\s+|(?<=죠)\s+|(?<=네)\s+",
        text,
    )
    sentences: list[str] = []
    for part in raw_parts:
        cleaned = part.strip()
        if len(cleaned) < 12:
            continue
        if _HOST_CHATTER.search(cleaned):
            continue
        sentences.append(cleaned)
    if not sentences:
        return text[:limit]
    picked: list[int] = []
    used: set[int] = set()
    scored = sorted(
        enumerate(sentences),
        key=lambda item: (
            sum(item[1].count(word) for word in _KEYWORDS),
            1 if 18 <= len(item[1]) <= 95 else 0,
        ),
        reverse=True,
    )
    for index, sentence in scored:
        score = sum(sentence.count(word) for word in _KEYWORDS)
        if score <= 0 and len(picked) >= 5:
            continue
        if index in used:
            continue
        used.add(index)
        picked.append(index)
        if sum(len(sentences[i]) + 2 for i in picked) >= limit - 120 or len(picked) >= 14:
            break
    ordered = [sentences[index] for index in sorted(picked)]
    body = "\n".join(f"- {line}" for line in ordered)
    return body[:limit]


def fetch_transcript(video_id: str) -> str:
    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()
    fetched = api.fetch(video_id, languages=("ko", "ko-KR", "en"))
    return " ".join(snippet.text.strip() for snippet in fetched if snippet.text and snippet.text.strip())


def _comments_from_api(video_id: str, api_key: str, limit: int) -> list[str]:
    response = httpx.get(
        "https://www.googleapis.com/youtube/v3/commentThreads",
        params={
            "part": "snippet",
            "videoId": video_id,
            "order": "relevance",
            "maxResults": limit,
            "textFormat": "plainText",
            "key": api_key,
        },
        timeout=20,
    )
    response.raise_for_status()
    comments = []
    for item in response.json().get("items") or []:
        snippet = item["snippet"]["topLevelComment"]["snippet"]
        text = (snippet.get("textDisplay") or "").replace("\n", " ").strip()
        if text:
            comments.append(text[:180])
    return comments


def _comments_from_page(video_id: str, limit: int) -> list[str]:
    from youtube_comment_downloader import SORT_BY_POPULAR, YoutubeCommentDownloader

    downloader = YoutubeCommentDownloader()
    generated = downloader.get_comments(video_id, sort_by=SORT_BY_POPULAR, language="ko")
    comments = []
    if generated is None:
        return comments
    for item in generated:
        if item.get("reply"):
            continue
        text = (item.get("text") or "").replace("\n", " ").strip()
        if text:
            comments.append(text[:180])
        if len(comments) >= limit:
            break
    return comments


def fetch_comments(video_id: str, settings: Settings, limit: int = 8) -> list[str]:
    if settings.youtube_api_key:
        try:
            return _comments_from_api(video_id, settings.youtube_api_key, limit)
        except Exception:
            pass
    box: dict[str, list[str]] = {}

    def run() -> None:
        try:
            box["comments"] = _comments_from_page(video_id, limit)
        except Exception:
            box["comments"] = []

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(25)
    return box.get("comments") or []


def fetch_player(video_id: str) -> dict:
    payload = {
        "videoId": video_id,
        "context": {
            "client": {
                "clientName": "ANDROID",
                "clientVersion": "20.10.38",
                "hl": "ko",
                "gl": "KR",
            }
        },
    }
    headers = {
        "User-Agent": "com.google.android.youtube/20.10.38 (Linux; U; Android 14)",
        "Content-Type": "application/json",
    }
    response = httpx.post("https://www.youtube.com/youtubei/v1/player", json=payload, headers=headers, timeout=20)
    response.raise_for_status()
    data = response.json()
    details = data.get("videoDetails") or {}
    micro = (data.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    view_count = details.get("viewCount")
    published = micro.get("publishDate") or micro.get("uploadDate") or ""
    return {
        "video_id": video_id,
        "channel_id": details.get("channelId") or "",
        "channel_name": details.get("author") or "",
        "title": details.get("title") or "",
        "description": details.get("shortDescription") or "",
        "published_at": published,
        "view_count": int(view_count) if str(view_count or "").isdigit() else None,
        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }


def fetch_view_counts(video_ids: list[str], api_key: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for start in range(0, len(video_ids), 50):
        chunk = video_ids[start:start + 50]
        response = httpx.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "statistics", "id": ",".join(chunk), "key": api_key},
            timeout=20,
        )
        response.raise_for_status()
        for item in response.json().get("items") or []:
            raw = (item.get("statistics") or {}).get("viewCount")
            if str(raw or "").isdigit():
                found[item["id"]] = int(raw)
    return found


def parse_published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def rising_window(published_at: datetime | None, now: datetime | None = None) -> bool:
    if published_at is None:
        return False
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    now = now or now_kst()
    return published_at >= now - timedelta(hours=36)


def annotate_rising(video: dict, settings: Settings, now: datetime | None = None) -> dict:
    now = now or now_kst()
    published = parse_published(video.get("published_at"))
    count = video.get("view_count")
    rate = None
    flag = 0
    if count is not None and published is not None:
        rate = round(views_per_hour(int(count), published, now), 1)
        flag = int(
            is_rising(
                int(count),
                published,
                now,
                min_views=settings.rising_min_views,
                max_views=settings.rising_max_views,
                min_per_hour=settings.rising_min_per_hour,
            )
        )
    video["views_per_hour"] = rate
    video["rising"] = flag
    return video


def material(title: str, description: str, excerpt: str, comments: list[str], summary: str = "") -> str:
    parts = [
        title.strip(),
        (description or "")[:800].strip(),
        (summary or "")[:2200].strip(),
        excerpt.strip(),
        "\n".join(comments),
    ]
    return "\n".join(part for part in parts if part)


def collect_context(video: dict, settings: Settings) -> VideoContext:
    transcript = ""
    try:
        transcript = fetch_transcript(video["video_id"])
    except Exception:
        transcript = ""
    comments = fetch_comments(video["video_id"], settings)
    feed_description = video.get("description") or ""
    player_description = ""
    description = feed_description
    if len(feed_description) < 80:
        try:
            player = fetch_player(video["video_id"])
            player_description = player.get("description") or ""
            if player_description:
                description = player_description
        except Exception:
            pass
    excerpt = build_excerpt(transcript) if transcript else (description or "")[:1500]
    if transcript:
        kind = "transcript"
    elif description:
        kind = "description"
    else:
        kind = "title"
    source = material(video.get("title") or "", description, excerpt, comments)
    return VideoContext(
        transcript=transcript,
        comments=comments,
        description=description,
        source_text=source,
        source_kind=kind,
        excerpt=excerpt,
        feed_description=feed_description,
        player_description=player_description,
    )
