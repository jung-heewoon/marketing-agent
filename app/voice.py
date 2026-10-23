from __future__ import annotations

import json
import random
import re
from pathlib import Path

from app.config import ROOT, Settings
from app.youtube import fetch_channels, fetch_comments

VOICE_PATH = ROOT / "data" / "voice.json"
_MARKERS = ("근데", "진짜", "ㅠ", "..", "네요", "더라고요", "거든요", "1인")
_SWEAR = re.compile(
    r"씨발|시발|ㅅㅂ|ㅆㅂ|병신|좆|존나|개새끼|지랄|씹|보지|자지|섹스|야동|미친년|미친놈|꺼져|닥쳐"
)
_LINK = re.compile(r"https?://|www\.|youtu\.be|#\S", re.I)
_EMOJI = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
_FRIEND = ("더라고요", "거든요")
_ASIDE = ("ㅠ", "..", "1인", "근데", "진짜")


def accept_comment(text: str) -> dict | None:
    raw = re.sub(r"\s+", " ", text or "").strip()
    if not 20 <= len(raw) <= 120:
        return None
    hangul = len(re.findall(r"[가-힣]", raw))
    letters = len(re.findall(r"[가-힣A-Za-z]", raw))
    if letters == 0 or hangul / letters < 0.7:
        return None
    if not any(marker in raw for marker in _MARKERS):
        return None
    if _SWEAR.search(raw) or _LINK.search(raw) or "운명원" in raw or re.search(r"습니다|입니다", raw):
        return None
    if len(_EMOJI.findall(raw)) >= 3:
        return None
    return {"text": raw, "tag": _tag(raw)}


def _tag(text: str) -> str:
    if any(marker in text for marker in _FRIEND):
        return "친구에게 말하듯"
    if any(marker in text for marker in _ASIDE):
        return "혼잣말"
    return "한숨"


def load_voice(path: Path | None = None) -> list[dict]:
    target = path or VOICE_PATH
    if not target.is_file():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    kept = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        tag = str(item.get("tag") or "").strip()
        if text and tag:
            kept.append({"text": text, "tag": tag, "channel": str(item.get("channel") or "")})
    return kept


def sample_diverse(items: list[dict], count: int = 3, rng: random.Random | None = None) -> list[dict]:
    picker = rng or random
    buckets: dict[str, list[dict]] = {}
    for item in items:
        buckets.setdefault(item["tag"], []).append(item)
    tags = list(buckets)
    picker.shuffle(tags)
    picked: list[dict] = []
    used = set()
    for tag in tags:
        if len(picked) >= count:
            break
        choice = picker.choice(buckets[tag])
        picked.append(choice)
        used.add(id(choice))
    rest = [item for item in items if id(item) not in used]
    picker.shuffle(rest)
    picked.extend(rest[: max(0, count - len(picked))])
    return picked[:count]


def pick_guidance(items: list[dict], roll: float, rng: random.Random | None = None) -> dict:
    if items:
        return {"kind": "casual", "samples": sample_diverse(items, 3, rng)}
    return {"kind": "plain"}


def harvest(settings: Settings, per_channel: int = 2, comment_limit: int = 15) -> list[dict]:
    videos, _warnings = fetch_channels(settings)
    grouped: dict[str, list[dict]] = {}
    for video in videos:
        bucket = grouped.setdefault(video.get("channel_id") or "", [])
        if len(bucket) < per_channel:
            bucket.append(video)
    found: list[dict] = []
    seen: set[str] = set()
    for group in grouped.values():
        for video in group:
            comments = fetch_comments(video["video_id"], settings, limit=comment_limit)
            for text in comments:
                item = accept_comment(text)
                if item is None or item["text"] in seen:
                    continue
                seen.add(item["text"])
                item["channel"] = video.get("channel_name") or ""
                found.append(item)
    return found


def save_voice(items: list[dict], path: Path | None = None) -> Path:
    target = path or VOICE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"items": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def main() -> None:
    from app.config import load_settings

    items = harvest(load_settings())
    path = save_voice(items)
    print(f"{len(items)}개를 {path}에 저장했습니다.")


if __name__ == "__main__":
    main()
