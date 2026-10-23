from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    dir: Path


@dataclass(frozen=True)
class Channel:
    id: str
    name: str


@dataclass(frozen=True)
class Settings:
    strata_base_url: str
    strata_model: str
    strata_timeout_sec: float
    host: str
    port: int
    min_chars: int
    max_chars: int
    brand: str
    max_retries: int
    daily_max_comments: int
    profiles: list[Profile]
    channels: list[Channel]
    test_video_ids: list[str]
    youtube_api_key: str
    videos_per_channel: int
    type_delay_ms: int
    hold_open_sec: int
    rising_min_views: int
    rising_max_views: int
    rising_min_per_hour: float
    livewiki_url: str
    livewiki_dir: Path
    laya_enabled: bool
    laya_device: str
    laya_repo: str
    laya_subfolder: str
    laya_candidates: int
    llm_provider: str
    gemini_base_url: str
    gemini_model: str
    gemini_api_key: str

    @property
    def channel_ids(self) -> set[str]:
        return {channel.id for channel in self.channels}

    def profile(self, profile_id: str) -> Profile | None:
        for profile in self.profiles:
            if profile.id == profile_id:
                return profile
        return None

    def channel_name(self, channel_id: str) -> str:
        for channel in self.channels:
            if channel.id == channel_id:
                return channel.name
        return channel_id


def load_settings(path: Path | None = None) -> Settings:
    raw_path = path or Path(os.environ.get("MARKETING_AGENT_CONFIG", ROOT / "config.yaml"))
    raw = yaml.safe_load(raw_path.read_text(encoding="utf-8")) or {}
    comment = raw.get("comment") or {}
    limits = raw.get("limits") or {}
    strata = raw.get("strata") or {}
    server = raw.get("server") or {}
    rising = raw.get("rising") or {}
    livewiki = raw.get("livewiki") or {}
    laya = raw.get("laya") or {}
    llm = raw.get("llm") or {}
    provider = str(llm.get("provider") or "strata").strip().lower()
    if provider not in {"strata", "gemini"}:
        provider = "strata"
    gemini_base = str(llm.get("gemini_base_url") or "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
    livewiki_dir = Path(livewiki.get("dir") or "data/chrome/livewiki")
    if not livewiki_dir.is_absolute():
        livewiki_dir = ROOT / livewiki_dir
    profiles = []
    for item in raw.get("profiles") or []:
        folder = Path(item["dir"])
        if not folder.is_absolute():
            folder = ROOT / folder
        profiles.append(Profile(id=item["id"], label=item.get("label") or item["id"], dir=folder))
    channels = [Channel(id=item["id"], name=item.get("name") or item["id"]) for item in raw.get("channels") or []]
    api_key = (raw.get("youtube_api_key") or "").strip() or os.environ.get("YOUTUBE_API_KEY", "").strip()
    return Settings(
        strata_base_url=str(strata.get("base_url") or "http://127.0.0.1:8080").rstrip("/"),
        strata_model=str(strata.get("model") or "strata"),
        strata_timeout_sec=float(strata.get("timeout_sec") or 180),
        host=str(server.get("host") or "127.0.0.1"),
        port=int(server.get("port") or 7860),
        min_chars=int(comment.get("min_chars") or 40),
        max_chars=int(comment.get("max_chars") or 120),
        brand=str(comment.get("brand") or "운명원"),
        max_retries=int(comment.get("max_retries") or 2),
        daily_max_comments=int(limits.get("daily_max_comments") or 10),
        profiles=profiles,
        channels=channels,
        test_video_ids=[str(item).strip() for item in (raw.get("test_video_ids") or []) if str(item).strip()],
        youtube_api_key=api_key,
        videos_per_channel=int(raw.get("videos_per_channel") or 5),
        type_delay_ms=int(raw.get("type_delay_ms") or 60),
        hold_open_sec=int(raw.get("hold_open_sec") or 8),
        rising_min_views=int(rising.get("min_views") or 1000),
        rising_max_views=int(rising.get("max_views") or 5000),
        rising_min_per_hour=float(rising.get("min_per_hour") or 150),
        livewiki_url=str(livewiki.get("home_url") or "https://livewiki.com/ko/"),
        livewiki_dir=livewiki_dir,
        laya_enabled=bool(laya.get("enabled", True)),
        laya_device=str(laya.get("device") or "cpu"),
        laya_repo=str(laya.get("repo") or "convaiinnovations/laya"),
        laya_subfolder=str(laya.get("subfolder") or "multilingual"),
        laya_candidates=int(laya.get("candidates") or 5),
        llm_provider=provider,
        gemini_base_url=gemini_base,
        gemini_model=str(llm.get("gemini_model") or "gemini-2.5-flash"),
        gemini_api_key=(os.environ.get("GEMINI_API_KEY") or "").strip(),
    )
