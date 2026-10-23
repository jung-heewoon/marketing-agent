from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import ROOT

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    return datetime.now(KST)


def db_path() -> Path:
    return Path(os.environ.get("MARKETING_AGENT_DB", ROOT / "data" / "agent.db"))


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS videos (
                video_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                published_at TEXT,
                thumbnail TEXT,
                url TEXT,
                view_count INTEGER,
                views_per_hour REAL,
                rising INTEGER NOT NULL DEFAULT 0,
                fetched_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL,
                concern TEXT,
                comment TEXT,
                evidence TEXT,
                source_text TEXT,
                source_kind TEXT,
                warnings TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                comment TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                screenshot TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(drafts)")}
        if "candidates" not in columns:
            conn.execute("ALTER TABLE drafts ADD COLUMN candidates TEXT")
        post_columns = {row[1] for row in conn.execute("PRAGMA table_info(posts)")}
        if "video_index" not in post_columns:
            conn.execute("ALTER TABLE posts ADD COLUMN video_index INTEGER")


def upsert_video(row: dict) -> None:
    with connect() as conn:
        current = conn.execute("SELECT view_count, views_per_hour, rising FROM videos WHERE video_id = ?", (row["video_id"],)).fetchone()
        view_count = row.get("view_count")
        views_per_hour = row.get("views_per_hour")
        rising = row.get("rising")
        if current is not None:
            if view_count is None:
                view_count = current["view_count"]
            if views_per_hour is None:
                views_per_hour = current["views_per_hour"]
            if rising is None:
                rising = current["rising"]
        if rising is None:
            rising = 0
        conn.execute(
            """
            INSERT INTO videos (
                video_id, channel_id, channel_name, title, description, published_at,
                thumbnail, url, view_count, views_per_hour, rising, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                channel_name = excluded.channel_name,
                title = excluded.title,
                description = excluded.description,
                published_at = excluded.published_at,
                thumbnail = excluded.thumbnail,
                url = excluded.url,
                view_count = excluded.view_count,
                views_per_hour = excluded.views_per_hour,
                rising = excluded.rising,
                fetched_at = excluded.fetched_at
            """,
            (
                row["video_id"],
                row["channel_id"],
                row["channel_name"],
                row["title"],
                row.get("description") or "",
                row.get("published_at") or "",
                row.get("thumbnail") or "",
                row.get("url") or "",
                view_count,
                views_per_hour,
                int(rising),
                row.get("fetched_at") or now_kst().isoformat(),
            ),
        )


def list_videos() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM videos ORDER BY published_at DESC").fetchall()
    return [dict(row) for row in rows]


def get_video(video_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()
    return dict(row) if row else None


def save_draft(row: dict) -> int:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO drafts (video_id, concern, comment, evidence, source_text, source_kind, warnings, candidates, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["video_id"],
                row.get("concern") or "",
                row.get("comment") or "",
                row.get("evidence") or "",
                row.get("source_text") or "",
                row.get("source_kind") or "",
                row.get("warnings") or "",
                _candidate_text(row.get("candidates")),
                now_kst().isoformat(),
            ),
        )
        return int(cur.lastrowid)


def _candidate_text(value) -> str:
    if isinstance(value, list):
        return json.dumps([str(item) for item in value], ensure_ascii=False)
    return str(value or "")


def save_draft_comment(video_id: str, comment: str) -> None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM drafts WHERE video_id = ? ORDER BY id DESC LIMIT 1",
            (video_id,),
        ).fetchone()
        if row is None:
            return
        conn.execute("UPDATE drafts SET comment = ? WHERE id = ?", (comment, row["id"]))


def latest_draft(video_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM drafts WHERE video_id = ? ORDER BY id DESC LIMIT 1",
            (video_id,),
        ).fetchone()
    return dict(row) if row else None


def channel_video_index(video_id: str) -> int | None:
    """같은 채널에서 최신 영상이 1번이다. 게시 시점의 순서를 기록에 고정한다."""
    video = get_video(video_id)
    if video is None:
        return None
    channel_id = video.get("channel_id") or ""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT video_id FROM videos
            WHERE channel_id = ?
            ORDER BY published_at DESC, video_id ASC
            """,
            (channel_id,),
        ).fetchall()
    for index, row in enumerate(rows, start=1):
        if row["video_id"] == video_id:
            return index
    return None


def add_post(row: dict) -> int:
    video_index = row.get("video_index")
    if video_index is None:
        video_index = channel_video_index(row["video_id"])
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO posts (
                video_id, profile_id, comment, status, reason, screenshot, video_index, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["video_id"],
                row["profile_id"],
                row["comment"],
                row["status"],
                row.get("reason") or "",
                row.get("screenshot") or "",
                video_index,
                now_kst().isoformat(),
            ),
        )
        return int(cur.lastrowid)


def recent_comments(limit: int = 8) -> list[str]:
    texts: list[str] = []
    try:
        with connect() as conn:
            posts = conn.execute(
                "SELECT comment FROM posts ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            drafts = conn.execute(
                "SELECT comment FROM drafts ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except sqlite3.Error:
        return []
    for row in list(posts) + list(drafts):
        text = str(row["comment"] or "").strip()
        if text and text not in texts:
            texts.append(text)
    return texts[:limit]


def list_posts(limit: int = 100, *, success_only: bool = False) -> list[dict]:
    where = "WHERE posts.status = 'success'" if success_only else ""
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT posts.*, videos.title, videos.channel_name
            FROM posts
            LEFT JOIN videos ON videos.video_id = posts.video_id
            {where}
            ORDER BY posts.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def _today_start() -> str:
    return now_kst().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def success_count_today() -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM posts WHERE status = 'success' AND created_at >= ?",
            (_today_start(),),
        ).fetchone()
    return int(row["n"])


def has_success(video_id: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM posts WHERE video_id = ? AND status = 'success' LIMIT 1",
            (video_id,),
        ).fetchone()
    return row is not None


def posted_video_ids() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT video_id FROM posts WHERE status = 'success'"
        ).fetchall()
    return [row["video_id"] for row in rows]


def posted_profiles_by_video() -> dict[str, list[str]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT video_id, profile_id
            FROM posts
            WHERE status = 'success'
            ORDER BY id ASC
            """
        ).fetchall()
    found: dict[str, list[str]] = {}
    for row in rows:
        bucket = found.setdefault(row["video_id"], [])
        if row["profile_id"] not in bucket:
            bucket.append(row["profile_id"])
    return found


def get_meta(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
