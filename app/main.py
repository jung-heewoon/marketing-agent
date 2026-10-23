from __future__ import annotations

import json
import re
import threading
from contextlib import asynccontextmanager
from dataclasses import replace

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import db, jobs
from app.config import ROOT, Profile, load_settings
from app.llm import ModelError, active_model_name, generate_draft, strata_status
from app.poster import PostError, fetch_livewiki_summary, open_livewiki_login, open_login, post_comment
from app.rules import Reject, assert_can_post, find_evidence, post_sentence, video_allowed
from app.youtube import (
    annotate_rising,
    collect_context,
    fetch_channels,
    fetch_player,
    fetch_view_counts,
    material,
    parse_published,
    parse_video_id,
    rising_window,
)

settings = load_settings()
browser_lock = threading.Lock()
draft_lock = threading.Lock()
inflight_lock = threading.Lock()
inflight: set[str] = set()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    (ROOT / "data" / "shots").mkdir(parents=True, exist_ok=True)
    for profile in settings.profiles:
        profile.dir.mkdir(parents=True, exist_ok=True)
    settings.livewiki_dir.mkdir(parents=True, exist_ok=True)
    db.init_db()
    yield


app = FastAPI(title="유튜브 댓글 에이전트", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


def _posted_map() -> dict[str, list[str]]:
    return db.posted_profiles_by_video()


def _limits_out() -> dict:
    return {
        "today": db.success_count_today(),
        "posted_video_ids": db.posted_video_ids(),
        "daily_max": settings.daily_max_comments,
    }


def _profile_label(profile_id: str) -> str:
    profile = settings.profile(profile_id)
    return profile.label if profile else profile_id


def _created_label(value: str) -> str:
    text = (value or "").strip()
    if len(text) < 16:
        return text
    return f"{text[:10].replace('-', '.')} {text[11:16]}"


def video_out(row: dict, posted: dict[str, list[str]] | None = None) -> dict:
    published = parse_published(row.get("published_at"))
    label = published.astimezone(db.KST).strftime("%Y.%m.%d %H:%M") if published else ""
    known = posted if posted is not None else _posted_map()
    return {
        "video_id": row["video_id"],
        "channel_id": row.get("channel_id") or "",
        "channel_name": row.get("channel_name") or "",
        "title": row.get("title") or "",
        "published_at": row.get("published_at") or "",
        "published_label": label,
        "thumbnail": row.get("thumbnail") or "",
        "url": row.get("url") or f"https://www.youtube.com/watch?v={row['video_id']}",
        "view_count": row.get("view_count"),
        "views_per_hour": row.get("views_per_hour"),
        "rising": bool(row.get("rising")),
        "posted_profiles": known.get(row["video_id"], []),
    }


def draft_out(row: dict) -> dict:
    warnings = [part for part in (row.get("warnings") or "").split("\n") if part]
    comment = row.get("comment") or ""
    evidence = row.get("evidence") or ""
    return {
        "video_id": row["video_id"],
        "concern": row.get("concern") or "",
        "comment": comment,
        "candidates": _load_candidates(row.get("candidates")),
        "evidence": evidence,
        "evidence_label": f"문자열 근거: {evidence}" if evidence else "",
        "source_kind": row.get("source_kind") or "",
        "warnings": warnings,
        "chars": len(comment.strip()),
    }


def _load_candidates(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if str(item).strip()]


def _choose_profile(requested: str) -> Profile:
    if not settings.profiles:
        raise Reject("사용할 크롬 프로필이 없습니다.")
    if requested and requested != "rotate":
        profile = settings.profile(requested)
        if profile is None:
            raise Reject("없는 프로필입니다.")
        return profile
    ids = [profile.id for profile in settings.profiles]
    last = db.get_meta("last_profile")
    if last in ids:
        profile_id = ids[(ids.index(last) + 1) % len(ids)]
    else:
        profile_id = ids[0]
    return settings.profile(profile_id)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health() -> dict:
    ok, detail = strata_status(settings)
    return {
        "ok": ok,
        "detail": detail,
        "model": active_model_name(settings),
        "provider": settings.llm_provider,
        **_limits_out(),
        "min_chars": settings.min_chars,
        "max_chars": settings.max_chars,
        "brand": settings.brand,
        "profiles": [{"id": profile.id, "label": profile.label} for profile in settings.profiles],
    }


@app.get("/api/videos")
def get_videos() -> dict:
    posted = _posted_map()
    return {"videos": [video_out(row, posted) for row in db.list_videos()]}


@app.post("/api/videos/fetch")
def fetch_videos() -> dict:
    videos, warnings = fetch_channels(settings)
    fetched_at = db.now_kst().isoformat()
    ids = []
    for video in videos:
        if not video.get("channel_id"):
            warnings.append(f"{video.get('title') or '영상'}의 채널을 확인하지 못해 건너뛰었습니다.")
            continue
        video["fetched_at"] = fetched_at
        db.upsert_video(video)
        ids.append(video["video_id"])
    posted = _posted_map()
    rows = [video_out(row, posted) for row in db.list_videos() if row["video_id"] in ids]
    return {"videos": rows, "warnings": warnings}


@app.post("/api/videos/resolve")
def resolve_video(body: dict) -> dict:
    video_id = parse_video_id(str(body.get("url") or ""))
    if not video_id:
        raise HTTPException(status_code=400, detail="영상 링크를 읽지 못했습니다.")
    existing = db.get_video(video_id)
    if existing and video_allowed(existing["video_id"], existing.get("channel_id") or "", settings):
        return {"video": video_out(existing)}
    try:
        player = fetch_player(video_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="영상 정보를 확인하지 못했습니다.") from exc
    channel_id = player.get("channel_id") or ""
    if not video_allowed(video_id, channel_id, settings):
        raise HTTPException(status_code=403, detail="허용된 채널의 영상이 아닙니다. 지정 채널 5개와 본인 테스트 영상만 열 수 있습니다.")
    row = {
        "video_id": video_id,
        "channel_id": channel_id,
        "channel_name": player.get("channel_name") or settings.channel_name(channel_id),
        "title": player.get("title") or video_id,
        "description": player.get("description") or "",
        "published_at": player.get("published_at") or (existing or {}).get("published_at") or "",
        "thumbnail": player.get("thumbnail") or "",
        "url": player.get("url") or "",
        "view_count": player.get("view_count"),
        "fetched_at": db.now_kst().isoformat(),
    }
    if row["view_count"] is not None and row["published_at"]:
        annotate_rising(row, settings)
    db.upsert_video(row)
    saved = db.get_video(video_id)
    return {"video": video_out(saved or row)}


@app.post("/api/videos/rising")
def find_rising() -> dict:
    videos = db.list_videos()
    now = db.now_kst()
    recent = []
    for video in videos:
        published = parse_published(video.get("published_at"))
        if rising_window(published, now):
            recent.append(video)
        elif video.get("rising"):
            video["rising"] = 0
            video["views_per_hour"] = None
            db.upsert_video(video)
    warnings: list[str] = []
    counts: dict[str, int] = {}
    if recent and settings.youtube_api_key:
        try:
            counts = fetch_view_counts([video["video_id"] for video in recent], settings.youtube_api_key)
        except Exception:
            warnings.append("YouTube Data API로 조회수를 가져오지 못했습니다.")
    for video in recent:
        count = counts.get(video["video_id"])
        if count is None and not settings.youtube_api_key:
            try:
                count = fetch_player(video["video_id"]).get("view_count")
            except Exception:
                warnings.append(f"{video.get('title') or video['video_id']} 조회수를 확인하지 못했습니다.")
                continue
        if count is None:
            continue
        video["view_count"] = count
        annotate_rising(video, settings, now)
        db.upsert_video(video)
    if not recent:
        warnings.append("최근 36시간 안에 올라온 영상이 없습니다. 먼저 영상 불러오기를 하세요.")
    posted = _posted_map()
    rows = [video_out(row, posted) for row in db.list_videos() if row.get("rising")]
    return {
        "videos": rows,
        "warnings": warnings,
        "note": "뜨기 직전 영상은 초안까지만 만듭니다. 자동으로 게시하지 않습니다.",
    }


@app.post("/api/drafts")
def create_draft(body: dict) -> dict:
    video_id = str(body.get("video_id") or "").strip()
    video = db.get_video(video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="목록에 없는 영상입니다. 먼저 불러오거나 링크를 입력하세요.")
    if not video_allowed(video["video_id"], video.get("channel_id") or "", settings):
        raise HTTPException(status_code=403, detail="허용된 채널의 영상이 아닙니다.")
    if not draft_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="초안을 이미 만들고 있습니다. 로컬 모델은 한 번에 하나만 처리합니다.")
    job = jobs.create("draft", "영상 내용 모으는 중")

    def run() -> None:
        try:
            ok, detail = strata_status(settings)
            if not ok:
                jobs.fail(job.id, detail)
                return
            jobs.update(job.id, step="유튜브 자막·댓글 모으는 중")
            context = collect_context(video, settings)
            if not browser_lock.acquire(blocking=False):
                if not context.transcript.strip() and len(context.description.strip()) < 20:
                    jobs.fail(job.id, "다른 크롬 작업이 진행 중입니다. 열린 크롬 창을 닫고 다시 누르세요.")
                    return
            else:
                try:
                    summary = fetch_livewiki_summary(
                        profile_dir=settings.livewiki_dir,
                        home_url=settings.livewiki_url,
                        video_url=video.get("url") or f"https://www.youtube.com/watch?v={video_id}",
                        shots_dir=ROOT / "data" / "shots",
                        video_id=video_id,
                        on_status=lambda step: jobs.update(job.id, step=step),
                    )
                    cleaned_summary = summary[:2000]
                    excerpt = context.excerpt if context.transcript.strip() else cleaned_summary[:1800]
                    kind = "livewiki+transcript" if context.transcript.strip() else "livewiki"
                    context = replace(
                        context,
                        summary=cleaned_summary,
                        excerpt=excerpt,
                        source_kind=kind,
                        source_text=material(
                            video.get("title") or "",
                            context.description,
                            excerpt,
                            context.comments,
                            cleaned_summary,
                        ),
                    )
                except PostError as exc:
                    if not context.transcript.strip() and len(context.description.strip()) < 20:
                        jobs.fail(job.id, exc.message, screenshot=exc.screenshot)
                        return
                finally:
                    browser_lock.release()
            draft = generate_draft(
                video,
                context,
                settings,
                on_status=lambda step: jobs.update(job.id, step=step),
            )
            db.save_draft(
                {
                    "video_id": video_id,
                    "concern": draft.get("concern") or "",
                    "comment": draft.get("comment") or "",
                    "evidence": draft.get("evidence") or "",
                    "source_text": draft.get("source_text") or context.source_text,
                    "source_kind": draft.get("source_kind") or context.source_kind,
                    "warnings": "\n".join(draft.get("warnings") or []),
                    "candidates": draft.get("candidates") or [],
                }
            )
            saved = db.latest_draft(video_id)
            result = draft_out(saved or {})
            if draft.get("pick_note"):
                result["pick_note"] = draft["pick_note"]
            jobs.finish(job.id, result=result, step="초안 준비됨")
        except (ModelError, Reject) as exc:
            jobs.fail(job.id, exc.message)
        except Exception as exc:
            jobs.fail(job.id, f"초안을 만들지 못했습니다. {exc}")
        finally:
            draft_lock.release()

    threading.Thread(target=run, daemon=True).start()
    return jobs.as_dict(job)


@app.get("/api/drafts/latest")
def get_latest_draft(video_id: str) -> dict:
    row = db.latest_draft(video_id)
    if row is None:
        raise HTTPException(status_code=404, detail="아직 초안이 없습니다.")
    return draft_out(row)


@app.post("/api/drafts/comment")
def save_comment(body: dict) -> dict:
    video_id = str(body.get("video_id") or "").strip()
    comment = str(body.get("comment") or "")
    draft = db.latest_draft(video_id)
    if draft is None:
        raise HTTPException(status_code=400, detail="먼저 초안을 만드세요.")
    db.save_draft_comment(video_id, comment)
    saved = db.latest_draft(video_id) or draft
    saved["comment"] = comment
    evidence = find_evidence(comment, saved.get("source_text") or "")
    saved["evidence"] = evidence
    return draft_out(saved)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="작업을 찾지 못했습니다.")
    return jobs.as_dict(job)


@app.post("/api/posts")
def create_post(body: dict) -> dict:
    video_id = str(body.get("video_id") or "").strip()
    comment = str(body.get("comment") or "").strip()
    video = db.get_video(video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="목록에 없는 영상입니다.")
    draft = db.latest_draft(video_id)
    if draft is None:
        raise HTTPException(status_code=400, detail="먼저 영상을 선택해 초안을 만드세요.")
    warnings = draft.get("warnings") or ""
    held = "게시 불가" in warnings or "게시 보류" in warnings or "검토 필요" in warnings
    saved = (draft.get("comment") or "").strip()
    if held and comment == saved:
        raise HTTPException(status_code=400, detail="이 초안은 게시할 수 없습니다. 고치거나 다시 만드세요.")
    source = draft.get("source_text") or ""
    evidence = find_evidence(comment, source)
    try:
        profile = _choose_profile(str(body.get("profile_id") or "youtube1"))
        assert_can_post(
            video_id=video_id,
            channel_id=video.get("channel_id") or "",
            comment=comment,
            source=source,
            evidence=evidence,
            settings=settings,
            success_today=db.success_count_today(),
            already_posted=db.has_success(video_id),
        )
    except Reject as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    with inflight_lock:
        if video_id in inflight:
            raise HTTPException(status_code=409, detail="이 영상 게시가 이미 진행 중입니다.")
        inflight.add(video_id)
    if not browser_lock.acquire(blocking=False):
        with inflight_lock:
            inflight.discard(video_id)
        raise HTTPException(status_code=409, detail="다른 크롬 작업이 진행 중입니다.")
    job = jobs.create("post", "프로필 여는 중")

    def run() -> None:
        try:
            post_comment(
                profile_dir=profile.dir,
                video_id=video_id,
                comment=comment,
                shots_dir=ROOT / "data" / "shots",
                delay_ms=settings.type_delay_ms,
                hold_open_sec=settings.hold_open_sec,
                on_status=lambda step: jobs.update(job.id, step=step),
            )
            db.add_post(
                {
                    "video_id": video_id,
                    "profile_id": profile.id,
                    "comment": comment,
                    "status": "success",
                    "reason": "",
                    "screenshot": "",
                }
            )
            db.set_meta("last_profile", profile.id)
            jobs.finish(job.id, result={"ok": True, "profile_id": profile.id}, step="게시 완료 · 댓글 보기")
        except PostError as exc:
            db.add_post(
                {
                    "video_id": video_id,
                    "profile_id": profile.id,
                    "comment": comment,
                    "status": "failed",
                    "reason": exc.message,
                    "screenshot": exc.screenshot or "",
                }
            )
            jobs.fail(job.id, exc.message, screenshot=exc.screenshot)
        except Exception as exc:
            db.add_post(
                {
                    "video_id": video_id,
                    "profile_id": profile.id,
                    "comment": comment,
                    "status": "failed",
                    "reason": str(exc),
                    "screenshot": "",
                }
            )
            jobs.fail(job.id, f"댓글 게시에 실패했습니다. {exc}")
        finally:
            with inflight_lock:
                inflight.discard(video_id)
            browser_lock.release()

    threading.Thread(target=run, daemon=True).start()
    return jobs.as_dict(job)


@app.get("/api/posts")
def get_posts() -> dict:
    rows = []
    for row in db.list_posts():
        index = row.get("video_index")
        if not index:
            index = db.channel_video_index(row["video_id"])
        label = _profile_label(row["profile_id"])
        channel_name = row.get("channel_name") or ""
        title = row.get("title") or ""
        comment = row["comment"]
        status = row["status"]
        reason = row.get("reason") or ""
        rows.append(
            {
                "id": row["id"],
                "video_id": row["video_id"],
                "title": title or row["video_id"],
                "channel_name": channel_name,
                "video_index": index,
                "profile_id": row["profile_id"],
                "profile_label": label,
                "comment": comment,
                "status": status,
                "reason": reason,
                "screenshot": row.get("screenshot") or "",
                "created_at": row["created_at"],
                "created_label": _created_label(row["created_at"]),
                "sentence": post_sentence(
                    label,
                    channel_name,
                    index,
                    comment,
                    title=title,
                    status=status,
                    reason=reason,
                ),
            }
        )
    return {"posts": rows, **_limits_out()}


@app.post("/api/livewiki/login")
def login_livewiki() -> dict:
    if not browser_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="다른 크롬 작업이 진행 중입니다.")
    job = jobs.create("livewiki", "LiveWiki 프로필 여는 중")

    def run() -> None:
        try:
            open_livewiki_login(
                settings.livewiki_dir,
                settings.livewiki_url,
                lambda step: jobs.update(job.id, step=step),
            )
            jobs.finish(job.id, step="LiveWiki 로그인 창을 닫았습니다.")
        except PostError as exc:
            jobs.fail(job.id, exc.message)
        except Exception as exc:
            jobs.fail(job.id, f"LiveWiki 로그인 창을 열지 못했습니다. {exc}")
        finally:
            browser_lock.release()

    threading.Thread(target=run, daemon=True).start()
    return jobs.as_dict(job)


@app.post("/api/profiles/{profile_id}/login")
def login_profile(profile_id: str) -> dict:
    profile = settings.profile(profile_id)
    if profile is None or profile_id == "rotate":
        raise HTTPException(status_code=404, detail="없는 프로필입니다.")
    if not browser_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="다른 크롬 작업이 진행 중입니다.")
    job = jobs.create("login", "프로필 여는 중")

    def run() -> None:
        try:
            open_login(profile.dir, lambda step: jobs.update(job.id, step=step))
            jobs.finish(job.id, step="로그인 창을 닫았습니다.")
        except PostError as exc:
            jobs.fail(job.id, exc.message)
        except Exception as exc:
            jobs.fail(job.id, f"로그인 창을 열지 못했습니다. {exc}")
        finally:
            browser_lock.release()

    threading.Thread(target=run, daemon=True).start()
    return jobs.as_dict(job)


@app.get("/api/shots/{name}")
def shot(name: str) -> FileResponse:
    if not re.fullmatch(r"[\w.-]+", name):
        raise HTTPException(status_code=404, detail="없는 화면 캡처입니다.")
    path = ROOT / "data" / "shots" / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="없는 화면 캡처입니다.")
    return FileResponse(path)
