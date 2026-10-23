from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.config import ROOT, Settings
from app.laya_judge import _ask, get_agent
from app.lines import instruction_residue, parse_comment_lines
from app.rules import _content_tokens, brand_is_subject, evidence_in_source, find_evidence, hard_errors, style_notes
from app.youtube import VideoContext

logger = logging.getLogger("app.llm")

READ_TEMPERATURE = 0.3
WRITE_TEMPERATURE = 0.8
WEAVE_TEMPERATURE = 0.8
REPAIR_TEMPERATURE = 0.3

_READ_SYSTEM = "영상을 보고 시청자의 고민을 정리한다."
_WRITE_FALLBACK = "이 영상을 본 한국 시청자"
_WEAVE_SYSTEM = "방금 댓글을 쓴 시청자다. 운명원에서 인연사주를 본다. 아직 안 봤다. 그 사람 인연으로 잇는다."
_PHRASES = ROOT / "data" / "brand_phrases.txt"
_SYNOPSIS = re.compile(r"설명합니다\s*[.!]?\s*$")
_PROMPT_ECHO = re.compile(
    r"고민\s*한\s*줄|한\s*줄에\s*하나씩|맨\s*위에\s*쓴다|보고\s*싶어지는\s*댓글|목록\s*문장만|문장\s*중간|붙일\s*문장만"
    r"|알다가도\s*모르겠|차이기만\s*했던\s*1인|한\s*문장|라고\s*쓴다|뒤에\s*붙일"
)
_SELF = re.compile(r"나는|나도|내가|나만|내\s|저는|저도|제가|저의|1인")
_BRAND_TOKENS = {"운명원", "연애사주", "인연사주", "사주"}
_LABEL = re.compile(r"^(고민|장면|시청자)\s*[:：]\s*(.+)$")
_LABEL_LINE = re.compile(r"^(?:고민|장면|시청자|말투)\s*[:：]")
_NO_VIEWER = "댓글 줄을 읽지 못했습니다. 다시 만들어 보세요."
_NO_CONCERN = "고민 한 줄을 읽지 못했습니다."
_SCENE_GAP = "영상 속 말을 확인하지 못했습니다. 댓글을 보고 고치세요."
_HELD = "게시 불가. 고치거나 다시 만드세요."
_REPAIR_BEFORE = "운명원은 인연이면 만나는 거라고 믿는 차이기만 했던 사람입니다."
_REPAIR_AFTER = "아 근데 운명원에서 저런 사람 인연은 잘 본다던데, 나도 한번 해볼까."
_RESULT = re.compile(r"봐\s*주(?:면|니까|어서|고)|봐\s*줬|명확|답이\s*나|보면|봤더니|알려\s*줬|나올\s*것")
_RUMOR = re.compile(r"던데|든데|들어|특화|모르|어디선가|좋다던")
_OPENER = re.compile(r"아\s*근데|근데|진짜|ㅠ|ㅜ|ㅋ|ㅎ|에휴|나만\s*그런가|1인|\.\.")
_STIFF_END = re.compile(r"(?:습니다|입니다|는다|ㄴ다|다)\s*[.…]?\s*$")
_AGREE = re.compile(r"공감|시원|라는\s*말")

# 앞 말의 단어를 다시 넣지 않고, 그 사람의 인연 소문으로 잇는다.
WEAVE_PAIRS = (
    (
        "연락 기다리는 건 진짜 별로예요 저도 겪어봤거든요",
        "아 근데 운명원에서 그런 사람 인연은 잘 본다던데 나도 한번 해볼까",
    ),
    (
        "답장 하나에 멘탈이 나가서 좋아하는 사람 앞에선 말이 안 나와요",
        "운명원에서 마음 약한 사람 인연도 잘 본다나 그냥 궁금해요",
    ),
    (
        "남자가 잘난 척하듯 설명하면 판 깔아주는 사람만 반응하는 거 같아요",
        "근데 운명원에서 저런 사람 인연은 잘 본다던데 한번 해볼까",
    ),
    (
        "차이고 나서야 왜 맨날 같은 사람만 만나나 싶어요",
        "운명원에서 그런 인연은 잘 본다던데 인연사주 쪽이라나",
    ),
)
_EXAMPLE_AFTERS = {re.sub(r"\s+", "", after) for _, after in WEAVE_PAIRS}
_EXAMPLE_AFTERS.add(re.sub(r"\s+", "", _REPAIR_AFTER))
_EXAMPLE_AFTERS.add(re.sub(r"\s+", "", _REPAIR_BEFORE))


class ModelError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class Ranked:
    comment: str
    hard: list[str]
    notes: list[str]
    score: int
    evidence: str


def parse_model_json(text: str) -> dict:
    raw = (text or "").strip()
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ModelError("모델이 JSON을 돌려주지 않았습니다.")
    data = json.loads(raw[start:end + 1])
    if not isinstance(data, dict):
        raise ModelError("모델 응답이 객체가 아닙니다.")
    comments = _comment_list(data)
    return {
        "topic": str(data.get("topic") or data.get("주제") or "").strip(),
        "concern": str(data.get("concern") or data.get("고민") or "").strip(),
        "comment": comments[0] if comments else "",
        "comments": comments,
        "evidence": str(data.get("evidence") or data.get("근거") or "").strip(),
    }


def _comment_list(data: dict) -> list[str]:
    raw = data.get("comments")
    if raw is None:
        raw = data.get("댓글들")
    if isinstance(raw, str):
        raw = [raw]
    comments = []
    for item in raw or []:
        text = str(item).strip()
        if text and text not in comments:
            comments.append(text)
    single = str(data.get("comment") or data.get("댓글") or "").strip()
    if single and single not in comments:
        comments.insert(0, single)
    return comments


def active_model_name(settings: Settings) -> str:
    if settings.llm_provider == "gemini":
        return settings.gemini_model
    return settings.strata_model


def completion_target(settings: Settings) -> tuple[str, dict, dict]:
    if settings.llm_provider == "gemini":
        return (
            f"{settings.gemini_base_url}/chat/completions",
            {"Authorization": f"Bearer {settings.gemini_api_key}"},
            {},
        )
    return (
        f"{settings.strata_base_url}/v1/chat/completions",
        {},
        {"reasoning_effort": "none"},
    )


def strata_status(settings: Settings) -> tuple[bool, str]:
    if settings.llm_provider == "gemini":
        if not settings.gemini_api_key:
            return False, "Gemini API 키가 없습니다. 환경변수 GEMINI_API_KEY를 설정하세요."
        return True, "Gemini가 연결되어 있습니다."
    try:
        response = httpx.get(f"{settings.strata_base_url}/health", timeout=2)
        if response.status_code < 500:
            return True, "로컬 모델이 연결되어 있습니다."
    except Exception:
        pass
    return False, "로컬 모델이 꺼져 있습니다. 바탕화면의 run-iq2_xs.bat 을 실행하세요."


def _error_message(response: httpx.Response) -> str:
    kind = ""
    detail = ""
    try:
        body = response.json()
    except Exception:
        body = {}
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        kind = str(error.get("type") or error.get("code") or "")
        detail = str(error.get("message") or "")[:300]
    logger.warning("model http %s type=%s detail=%s", response.status_code, kind or "-", detail or "-")
    if kind == "structured_output_failed":
        return "로컬 모델이 JSON 검증에 실패했습니다. structured_output_failed"
    if response.status_code == 502:
        return f"로컬 모델 서버 오류입니다. {kind or '원인 불명'}"
    return f"로컬 모델이 요청을 거절했습니다. {response.status_code}" + (f" {kind}" if kind else "")


def _complete(settings: Settings, messages: list[dict], max_tokens: int = 900, temperature: float = 0.7) -> str:
    if settings.llm_provider == "gemini" and not settings.gemini_api_key:
        raise ModelError("Gemini API 키가 없습니다. 환경변수 GEMINI_API_KEY를 설정하세요.")
    url, headers, extra = completion_target(settings)
    payload = {
        "model": active_model_name(settings),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        **extra,
    }
    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=settings.strata_timeout_sec)
    except httpx.ConnectError as exc:
        raise ModelError("로컬 모델 서버에 연결하지 못했습니다. run-iq2_xs.bat 을 실행하세요.") from exc
    except httpx.TimeoutException as exc:
        raise ModelError("로컬 모델 응답이 너무 오래 걸렸습니다. 다시 만들어 보세요.") from exc
    if response.status_code >= 400:
        raise ModelError(_error_message(response))
    try:
        body = response.json()
        return body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ModelError("로컬 모델 응답 형식을 읽지 못했습니다.") from exc


def shown_description(context: VideoContext) -> str:
    feed = (context.feed_description or "").strip()
    player = (context.player_description or "").strip()
    if feed and len(feed) >= 80:
        return feed
    if player:
        return player
    return (context.description or feed).strip()


def seen_material(video: dict, context: VideoContext) -> str:
    """모델 프롬프트에 넣은 제목, 설명, 요약, 발췌만. 댓글은 근거가 아니다."""
    parts = [
        (video.get("title") or "").strip(),
        shown_description(context)[:400].strip(),
        (context.summary or "").strip()[:700],
        (context.excerpt or "").strip()[:900],
    ]
    return "\n".join(part for part in parts if part)


def load_brand_phrases() -> list[str]:
    if not _PHRASES.exists():
        return []
    lines: list[str] = []
    for row in _PHRASES.read_text(encoding="utf-8").splitlines():
        line = row.strip()
        if not line or line.startswith("#") or line in lines:
            continue
        lines.append(line)
    return lines


def _usable_concern(concern: str, title: str) -> str:
    text = (concern or "").strip()
    if not text or text == (title or "").strip() or _SYNOPSIS.search(text):
        return ""
    return text


def parse_reading(raw: str, seen: str) -> dict:
    concern = ""
    scenes: list[str] = []
    persona = ""
    for line in parse_comment_lines(raw):
        matched = _LABEL.match(line)
        if not matched:
            continue
        key, value = matched.group(1), matched.group(2).strip()
        if key == "고민":
            concern = _usable_concern(value, "")
        elif key == "장면":
            for part in re.split(r"\s*[|/]\s*", value):
                part = part.strip().strip("\"'“”")
                if part and part not in scenes:
                    scenes.append(part)
        elif key == "시청자":
            persona = value
    verified = [scene for scene in scenes if evidence_in_source(scene, seen)]
    return {"concern": concern, "scenes": verified[:2], "persona": persona}


def has_opener(text: str) -> bool:
    return _OPENER.search((text or "").strip()[:18]) is not None


def voice_lines(context: VideoContext, settings: Settings, limit: int = 5) -> list[str]:
    """이 영상 댓글만. 다른 채널에서 모아 둔 문장은 넣지 않는다."""
    picked: list[str] = []
    for raw in context.comments or []:
        text = str(raw).strip()
        if not text or settings.brand in text or text in picked:
            continue
        picked.append(text)
        if len(picked) >= limit:
            break
    return picked


def build_read_messages(video: dict, context: VideoContext) -> list[dict]:
    title = (video.get("title") or "").strip()
    comments = [str(line).strip() for line in (context.comments or []) if str(line).strip()][:5]
    comment_block = "\n".join(f"- {line}" for line in comments) or "- 없음"
    user = f"""아래 이름표로만 답한다.
고민: 시청자 본인이 겪는 일 한 줄
장면: 자료에 있는 말을 그대로. 두 줄.
시청자: 나이대, 성별, 지금 상황

[영상]
제목: {title}
자막: {(context.excerpt or "").strip()[:900]}
설명: {shown_description(context)[:400]}
요약: {(context.summary or "").strip()[:700]}
댓글:
{comment_block}"""
    return [
        {"role": "system", "content": _READ_SYSTEM},
        {"role": "user", "content": user},
    ]


def build_messages(
    video: dict,
    context: VideoContext,
    settings: Settings,
    persona: str = "",
    scenes: list[str] | None = None,
    concern: str = "",
) -> list[dict]:
    who = (persona or "").strip() or _WRITE_FALLBACK
    del scenes, concern
    title = (video.get("title") or "").strip()
    if (context.excerpt or "").strip():
        body_label, body = "자막", context.excerpt.strip()[:900]
    else:
        body_label, body = "요약", (context.summary or context.transcript or "").strip()[:900]
    comments = voice_lines(context, settings)
    if comments:
        comment_block = "이 영상 댓글:\n" + "\n".join(f"- {line}" for line in comments)
        tone = "위에 달린 댓글 말투로. 한국 사람이 유튜브에 달 때처럼, ㅋㅋ ㅠ 진짜 근데 같은 말도 그냥 나와도 됨.\n3개."
    else:
        comment_block = "이 영상에는 댓글이 없다. 요약을 보고 방금 본 사람처럼 쓴다."
        tone = "한국 사람이 유튜브에 다는 말투로. ㅋㅋ ㅠ 진짜 근데, 끝이 좀 풀려도 됨.\n3개."
    user = f"""제목: {title}
{body_label}: {body}

{comment_block}

{tone}"""
    return [
        {"role": "system", "content": f"너는 {who}다. 이 영상을 방금 봤다."},
        {"role": "user", "content": user},
    ]


def build_weave_messages(comment: str, concern: str = "") -> list[dict]:
    del concern
    messages: list[dict] = [{"role": "system", "content": _WEAVE_SYSTEM}]
    for before, after in WEAVE_PAIRS:
        messages.append({"role": "user", "content": before})
        messages.append({"role": "assistant", "content": after})
    messages.append({"role": "user", "content": comment.strip()})
    return messages


_BREATH_SPLIT = re.compile(r"(?<=[요죠네다까지음어야])\s*(?:[.!?…]+|[ㅠㅜ]+)\s+")


def breaths(text: str) -> list[str]:
    """말의 숨 고르는 곳만 나눈다. 단어 중간은 자르지 않는다."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return []
    cuts = [match.end() for match in _BREATH_SPLIT.finditer(raw)]
    if not cuts:
        return [raw]
    pieces: list[str] = []
    start = 0
    for end in cuts:
        piece = raw[start:end].strip()
        if piece:
            pieces.append(piece)
        start = end
    rest = raw[start:].strip()
    if rest:
        pieces.append(rest)
    return pieces


def one_breath(text: str, limit: int, seen: str = "", scenes: list[str] | None = None) -> str:
    """뒷말을 붙일 자리가 남도록, 긴 댓글은 숨 하나까지 줄인다."""
    raw = (text or "").strip()
    pieces = breaths(raw)
    if len(pieces) <= 1:
        return raw
    room = max(24, limit - 45)
    fitting = [piece for piece in pieces if len(piece) <= room]
    pool = fitting or [min(pieces, key=len)]
    scored = scenes or []
    pool.sort(key=lambda piece: (_viewer_score(piece, seen, scored), pieces.index(piece)), reverse=True)
    return pool[0]


def _clauses(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,，]", text or "") if part.strip()]


def _shrink_from_right(text: str, budget: int) -> str:
    if budget <= 0 or not text:
        return ""
    if len(text) <= budget:
        return text
    parts = _clauses(text)
    if len(parts) <= 1:
        return ""
    chosen: list[str] = []
    best = ""
    for part in reversed(parts):
        chosen.append(part)
        trial = ", ".join(reversed(chosen))
        if len(trial) <= budget:
            best = trial
        else:
            break
    return best


def _shrink_tail(head: str, tail: str, limit: int) -> str:
    chosen: list[str] = []
    best = ""
    for part in _clauses(tail):
        chosen.append(part)
        trial_tail = ", ".join(chosen)
        if "운명원" not in trial_tail:
            continue
        trial = f"{head} {trial_tail}".strip() if head else trial_tail
        if len(trial) <= limit:
            best = trial
        else:
            break
    return best


def fit_comment(text: str, limit: int = 120) -> str:
    """120자가 넘으면 운명원 바로 앞의 말만 남긴다."""
    body = re.sub(r"\s+", " ", (text or "").strip())
    if len(body) <= limit:
        return body
    index = body.find("운명원")
    if index < 0:
        pieces = breaths(body)
        chosen: list[str] = []
        best = ""
        for piece in reversed(pieces):
            chosen.append(piece)
            trial = " ".join(reversed(chosen))
            if len(trial) <= limit:
                best = trial
            else:
                break
        return best or body
    tail = body[index:].strip()
    pieces = breaths(body[:index].strip())
    chosen = []
    best = ""
    for piece in reversed(pieces):
        chosen.append(piece)
        trial = f"{' '.join(reversed(chosen))} {tail}".strip()
        if len(trial) <= limit:
            best = trial
        else:
            break
    if best:
        return best
    piece = pieces[-1] if pieces else ""
    budget = limit - len(tail) - 1
    shrunk = _shrink_from_right(piece, budget)
    trial = f"{shrunk} {tail}".strip() if shrunk else ""
    if trial and len(trial) <= limit:
        return trial
    return _shrink_tail(shrunk, tail, limit) or tail[:limit].rstrip()


def join_comment(viewer: str, tail: str) -> str:
    """앞 댓글은 그대로 두고, 뒷문장만 뒤에 붙인다."""
    head = (viewer or "").strip()
    rest = followup_sentence(tail, head)
    if not head:
        return rest
    if not rest:
        return head
    return f"{head} {rest}"


def claims_result(text: str) -> bool:
    """보면 답이 나온다는 식은 가본 사람 말이다."""
    return _RESULT.search(text or "") is not None


def hearsay(text: str) -> bool:
    """어디선가 들었다는 말인지. 없으면 탈락시키지 않고 점수만 올린다."""
    return _RUMOR.search(text or "") is not None


def _example_copy(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    return compact in _EXAMPLE_AFTERS


def followup_sentence(line: str, viewer: str) -> str:
    """뒷문장은 운명원으로 시작하는 한 문장만 남긴다."""
    text = (line or "").strip()
    head = (viewer or "").strip()
    if not text:
        return ""
    if head and text.startswith(head):
        text = text[len(head):].lstrip(" \n\t.,!?…")
    index = text.find("운명원")
    if index < 0:
        return ""
    lead = text[:index].strip()
    if lead and not re.match(r"아?\s*근데", lead):
        text = text[index:].strip()
    return text.strip()


def build_repair_messages(comment: str, errors: list[str]) -> list[dict]:
    notes = "\n".join(f"- {item}" for item in errors) or "- 한자 없이 짧게"
    user = f"""[뒷문장]
{comment.strip()}

[고칠 점]
{notes}"""
    return [
        {"role": "system", "content": _WEAVE_SYSTEM},
        {"role": "user", "content": _REPAIR_BEFORE},
        {"role": "assistant", "content": _REPAIR_AFTER},
        {"role": "user", "content": user},
    ]


def without_brand_as_person(lines: list[str], viewer: str = "") -> list[str]:
    """운명원을 사람 주어로 쓴 줄은 고르지 않는다. 없으면 시청자 글을 남긴다."""
    kept = [line.strip() for line in lines if (line or "").strip() and not brand_is_subject(line)]
    if kept:
        return kept
    own = (viewer or "").strip()
    if own and not brand_is_subject(own):
        return [own]
    return []


def _prompt_echo(line: str) -> bool:
    text = line or ""
    if instruction_residue(text):
        return True
    compact = re.sub(r"\s+", "", text)
    if compact in _EXAMPLE_AFTERS:
        return True
    return bool(_PROMPT_ECHO.search(text))


def copies_shown(text: str, sources: list[str] | None) -> bool:
    """보여 준 댓글이나 자막과 거의 같은 줄."""
    body = re.sub(r"[^0-9A-Za-z가-힣]", "", text or "")
    if len(body) < 8:
        return False
    for source in sources or []:
        other = re.sub(r"[^0-9A-Za-z가-힣]", "", source or "")
        if len(other) < 8:
            continue
        if body in other or other in body:
            short, long = sorted((len(body), len(other)))
            if long and short / long >= 0.7:
                return True
    return False


def pick_viewer(
    lines: list[str],
    seen: str,
    scenes: list[str],
    brand: str,
    shown: list[str] | None = None,
) -> str:
    pool: list[str] = []
    for line in lines:
        text = (line or "").strip()
        if not text or _prompt_echo(text) or _LABEL_LINE.match(text) or brand in text or _SYNOPSIS.search(text):
            continue
        if copies_shown(text, shown):
            continue
        pool.append(text)
    if not pool:
        return ""
    pool.sort(key=lambda text: _viewer_score(text, seen, scenes), reverse=True)
    return pool[0]


def keeps_viewer(line: str, viewer: str) -> bool:
    if not (viewer or "").strip():
        return True
    return bool(find_evidence(line, viewer))


def _repeats_recent(text: str, recent: list[str]) -> bool:
    index = text.find("운명원")
    if index < 0:
        return False
    tail = re.sub(r"\s+", "", text[index:index + 18])
    if len(tail) < 8:
        return False
    for old in recent:
        if tail[:12] in re.sub(r"\s+", "", old or ""):
            return True
    return False


def _scene_hit(text: str, scenes: list[str]) -> bool:
    return any(evidence_in_source(scene, text) for scene in scenes)


def _evidence_for(comment: str, seen: str, scenes: list[str]) -> str:
    for scene in scenes:
        if evidence_in_source(scene, comment):
            return scene[:90]
    return find_evidence(comment, seen)


def _self_voice(text: str) -> bool:
    return _SELF.search(text or "") is not None


def _lead(text: str) -> str:
    head = (text or "").split("운명원")[0]
    piece = re.split(r"[,.!?]", head, maxsplit=1)[0]
    return piece.strip() or head.strip()


def _overlap(text: str, scenes: list[str], seen: str) -> tuple[float, int]:
    source = ""
    for scene in scenes:
        if evidence_in_source(scene, text):
            source = scene
            break
    if not source:
        source = find_evidence(text, seen)
    if not source:
        return 0.0, 0
    tokens = [token for token in _content_tokens(text) if token not in _BRAND_TOKENS]
    if not tokens:
        return 0.0, 0
    src_tokens = _content_tokens(source)
    hits = 0
    for token in tokens:
        if token in source or any(len(token) >= 2 and len(other) >= 2 and token[:2] == other[:2] for other in src_tokens):
            hits += 1
    return hits / len(tokens), hits


def _paraphrase(text: str, scenes: list[str], seen: str) -> bool:
    ratio, _hits = _overlap(text, scenes, seen)
    lead_ratio, lead_hits = _overlap(_lead(text), scenes, seen)
    return ratio >= 0.6 or (lead_hits >= 3 and lead_ratio >= 0.5)


def _ground_score(text: str, scenes: list[str], seen: str) -> int:
    if _paraphrase(text, scenes, seen):
        return -4
    ratio, hits = _overlap(text, scenes, seen)
    if 2 <= hits <= 4 and ratio < 0.55:
        return 3
    if hits >= 2 or _scene_hit(text, scenes) or find_evidence(text, seen):
        return 1
    return 0


def _brand_sits_inside(text: str) -> bool:
    index = (text or "").find("운명원")
    if index < 0:
        return False
    rest = text[index + len("운명원"):]
    return bool(re.search(r"[,.].{6,}|다가\s+\S{2,}", rest))


def _agrees_with_video(text: str) -> bool:
    return _AGREE.search(text or "") is not None


def _brand_after_break(text: str) -> bool:
    index = (text or "").find("운명원")
    if index < 8:
        return False
    return bool(re.search(r"[.!?…]\s*\S", text[:index]))


def _stiff_end(text: str) -> bool:
    return _STIFF_END.search((text or "").strip()) is not None


def _viewer_score(text: str, seen: str, scenes: list[str]) -> int:
    score = 3 if _self_voice(text) else 0
    if has_opener(text):
        score += 2
    if _agrees_with_video(text):
        score -= 4
    if _stiff_end(text):
        score -= 3
    return score + _ground_score(text, scenes, seen)


def _score(text: str, seen: str, scenes: list[str], recent: list[str], notes: list[str]) -> int:
    score = _ground_score(text, scenes, seen)
    if _self_voice(text):
        score += 3
    if has_opener(text):
        score += 2
    if _agrees_with_video(text):
        score -= 4
    if _brand_after_break(text):
        score += 2
    if hearsay(text):
        score += 2
    index = text.find("운명원")
    if index > 10:
        score += 1
    elif index == 0:
        score -= 2
    if _brand_sits_inside(text):
        score += 1
    if "연애사주" in text or "인연사주" in text:
        score += 1
    if brand_is_subject(text):
        score -= 4
    length = len(text.strip())
    if 60 <= length <= 110:
        score += 2
    elif 40 <= length <= 120:
        score += 1
    if _repeats_recent(text, recent):
        score -= 2
    else:
        score += 1
    score -= len(notes)
    return score


def _laya_bonus(agent, text: str, settings: Settings, seen: str) -> int:
    human, pull, video, _confidence = _ask(agent, text, settings.brand, seen[:500])
    if human == "YES" and pull == "YES" and video == "YES":
        return 2
    if pull == "YES":
        return 1
    return 0


def rank_comments(
    comments: list[str],
    seen: str,
    scenes: list[str],
    settings: Settings,
    recent: list[str] | None = None,
    agent=None,
) -> Ranked | None:
    unique: list[str] = []
    for item in comments:
        text = (item or "").strip()
        if text and text not in unique and not _prompt_echo(text):
            unique.append(text)
    if not unique:
        return None
    ranked: list[Ranked] = []
    remembered = list(recent or [])
    for text in unique:
        hard = hard_errors(text, settings)
        notes = style_notes(text, settings)
        score = _score(text, seen, scenes, remembered, notes)
        if agent is not None and not hard:
            score += _laya_bonus(agent, text, settings, seen)
        ranked.append(Ranked(text, hard, notes, score, _evidence_for(text, seen, scenes)))
    ranked.sort(key=lambda item: (1 if item.hard else 0, -item.score))
    return ranked[0]


def _maybe_agent(settings: Settings):
    if not settings.laya_enabled:
        return None
    agent, _note = get_agent(settings)
    return agent


def hold_without_transcript(kind: str, comment: str, ok: bool, warnings: list[str]) -> tuple[bool, list[str]]:
    if kind not in {"title", "description", "livewiki"} or not (comment or "").strip():
        return ok, warnings
    kept = list(warnings)
    note = "검토 필요. 자막이 없어 댓글을 확인하고 고친 뒤 게시하세요."
    if note not in kept:
        kept.append(note)
    return False, kept


def _empty_draft(concern: str, seen: str, context: VideoContext, warnings: list[str], stages: dict) -> dict:
    return {
        "concern": concern,
        "comment": "",
        "candidates": [],
        "evidence": "",
        "warnings": warnings,
        "chars": 0,
        "source_kind": context.source_kind,
        "source_text": seen,
        "pick_note": "",
        "stages": stages,
    }


def _pack(
    choice: Ranked,
    *,
    concern: str,
    seen: str,
    context: VideoContext,
    candidates: list[str],
    stages: dict,
) -> dict:
    warnings: list[str] = []
    if choice.hard:
        warnings.append(_HELD)
        warnings.extend(choice.hard)
    warnings.extend(choice.notes)
    if not choice.evidence:
        warnings.append(_SCENE_GAP)
    if not concern.strip():
        warnings.append(_NO_CONCERN)
    _, warnings = hold_without_transcript(context.source_kind, choice.comment, True, warnings)
    if choice.hard:
        note = "규칙을 다 맞추지 못한 문장"
    elif _self_voice(choice.comment) and choice.evidence:
        note = "내 경험으로 공감한 문장"
    elif choice.evidence:
        note = "영상 속 말을 짚은 문장"
    else:
        note = "시청자 댓글에 운명원을 녹인 문장"
    return {
        "concern": concern,
        "comment": choice.comment,
        "candidates": candidates,
        "evidence": choice.evidence,
        "warnings": warnings,
        "chars": len(choice.comment.strip()),
        "source_kind": context.source_kind,
        "source_text": seen,
        "pick_note": note,
        "stages": stages,
    }


def _read_video(
    video: dict,
    context: VideoContext,
    seen: str,
    settings: Settings,
    stages: dict,
    on_status: Callable[[str], None] | None,
) -> dict:
    messages = build_read_messages(video, context)
    reading = {"concern": "", "scenes": [], "persona": ""}
    for batch in range(1, 3):
        if on_status:
            on_status(f"영상 읽는 중 ({batch}/2)")
        raw = _complete(settings, messages, max_tokens=500, temperature=READ_TEMPERATURE)
        stages["read"] = raw
        reading = parse_reading(raw, seen)
        logger.info("읽기 고민=%s 장면=%s", bool(reading["concern"]), len(reading["scenes"]))
        if reading["concern"] or reading["scenes"]:
            break
    return reading


def _write_viewer(
    video: dict,
    context: VideoContext,
    settings: Settings,
    reading: dict,
    seen: str,
    stages: dict,
    on_status: Callable[[str], None] | None,
    on_attempt: Callable[[int, int], None] | None,
) -> str:
    messages = build_messages(
        video,
        context,
        settings,
        persona=reading.get("persona") or "",
        scenes=reading.get("scenes") or [],
        concern=reading.get("concern") or "",
    )
    viewer = ""
    for batch in range(1, 3):
        if on_status:
            on_status(f"시청자 댓글 쓰는 중 ({batch}/2)")
        if on_attempt:
            on_attempt(batch, 2)
        raw = _complete(settings, messages, max_tokens=700, temperature=WRITE_TEMPERATURE)
        stages["write"] = raw
        shown = [str(line).strip() for line in (context.comments or []) if str(line).strip()]
        for piece in (context.excerpt, context.summary, context.transcript):
            if (piece or "").strip():
                shown.append(piece.strip())
        viewer = pick_viewer(
            parse_comment_lines(raw),
            seen,
            reading.get("scenes") or [],
            settings.brand,
            shown,
        )
        logger.info("시청자 줄=%s", bool(viewer))
        if viewer:
            break
    return viewer


def _weave(
    viewer: str,
    concern: str,
    settings: Settings,
    stages: dict,
    on_status: Callable[[str], None] | None,
    on_attempt: Callable[[int, int], None] | None,
) -> list[str]:
    messages = build_weave_messages(viewer, concern)
    parsed: list[str] = []
    for batch in range(1, 3):
        if on_status:
            on_status(f"뒷문장 쓰는 중 ({batch}/2)")
        if on_attempt:
            on_attempt(batch, 2)
        raw = _complete(settings, messages, max_tokens=500, temperature=WEAVE_TEMPERATURE)
        stages["weave"] = raw
        fresh: list[str] = []
        copied: list[str] = []
        for line in parse_comment_lines(raw):
            tail = followup_sentence(line, viewer)
            if not tail or claims_result(tail) or settings.brand not in tail or brand_is_subject(tail):
                continue
            if _prompt_echo(tail) and not _example_copy(tail):
                continue
            bucket = copied if _example_copy(tail) else fresh
            if tail not in bucket:
                bucket.append(tail)
        parsed = fresh or copied
        logger.info("뒷문장=%s", len(parsed))
        if parsed:
            break
    return parsed


def _repair(viewer: str, comment: str, errors: list[str], settings: Settings, stages: dict) -> str:
    tail = followup_sentence(comment, viewer) or comment
    raw = _complete(
        settings,
        build_repair_messages(tail, errors),
        max_tokens=300,
        temperature=REPAIR_TEMPERATURE,
    )
    stages["repair"] = raw
    joined: list[str] = []
    for line in parse_comment_lines(raw):
        piece = followup_sentence(line, viewer)
        if not piece or claims_result(piece) or settings.brand not in piece or brand_is_subject(piece):
            continue
        if _prompt_echo(piece) and not _example_copy(piece):
            continue
        whole = join_comment(viewer, piece)
        if whole and not brand_is_subject(whole):
            joined.append(whole)
    clean = [line for line in joined if not hard_errors(line, settings)]
    return clean[0] if clean else (joined[0] if joined else "")


def _take_attempt(new: Ranked | None, old: Ranked | None) -> Ranked | None:
    """규칙을 통과한 초안을 먼저 남긴다. 둘 다 실패면 문제가 적은 쪽을 남긴다."""
    if new is None:
        return old
    if old is None:
        return new
    if not new.hard and old.hard:
        return new
    if new.hard and not old.hard:
        return old
    if len(new.hard) != len(old.hard):
        return new if len(new.hard) < len(old.hard) else old
    return new if new.score > old.score else old


def _compose_once(
    video: dict,
    context: VideoContext,
    settings: Settings,
    reading: dict,
    seen: str,
    stages: dict,
    on_status: Callable[[str], None] | None,
    on_attempt: Callable[[int, int], None] | None,
) -> tuple[Ranked | None, list[str], str]:
    """댓글을 처음부터 한 번 만든다. 이전 실패 문장은 넣지 않는다."""
    concern = reading.get("concern") or ""
    scenes = reading.get("scenes") or []
    viewer = _write_viewer(video, context, settings, reading, seen, stages, on_status, on_attempt)
    if not viewer:
        return None, [], ""
    viewer = one_breath(viewer, settings.max_chars, seen, scenes)
    tails = _weave(viewer, concern, settings, stages, on_status, on_attempt)
    pool = []
    for tail in tails:
        whole = fit_comment(join_comment(viewer, tail), settings.max_chars)
        if whole and settings.brand in whole and not brand_is_subject(whole) and whole not in pool:
            pool.append(whole)
    if not pool:
        pool = [fit_comment(join_comment(viewer, _REPAIR_AFTER), settings.max_chars)]
    choice = rank_comments(pool, seen, scenes, settings)
    return choice, pool, viewer


def generate_draft(
    video: dict,
    context: VideoContext,
    settings: Settings,
    on_attempt: Callable[[int, int], None] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> dict:
    ok, detail = strata_status(settings)
    if not ok:
        raise ModelError(detail)
    if not context.source_text.strip():
        raise ModelError("이 영상에서 제목, 설명, 자막을 읽지 못했습니다.")
    seen = seen_material(video, context)
    stages: dict[str, str] = {}
    reading = _read_video(video, context, seen, settings, stages, on_status)
    concern = reading.get("concern") or ""
    scenes = reading.get("scenes") or []
    choice: Ranked | None = None
    pool: list[str] = []
    viewer = ""
    for turn in range(3):
        if turn and on_status:
            on_status(f"처음부터 다시 만드는 중 ({turn}/2)")
        made, made_pool, made_viewer = _compose_once(
            video, context, settings, reading, seen, stages, on_status, on_attempt,
        )
        if _take_attempt(made, choice) is made and made is not None:
            choice, pool, viewer = made, made_pool, made_viewer
        if choice is not None and not choice.hard:
            break
    if choice is None:
        warnings = [_NO_VIEWER]
        if not concern.strip():
            warnings.append(_NO_CONCERN)
        return _empty_draft(concern, seen, context, warnings, stages)
    if brand_is_subject(choice.comment):
        own = without_brand_as_person([], viewer)
        replaced = rank_comments(own, seen, scenes, settings)
        if replaced is not None:
            choice = replaced
            pool = own
    elif not choice.hard and pool:
        judged = rank_comments(pool, seen, scenes, settings, agent=_maybe_agent(settings))
        if judged is not None and not judged.hard:
            choice = judged
    fitted = fit_comment(choice.comment, settings.max_chars)
    if fitted and fitted != choice.comment and settings.brand in fitted and not brand_is_subject(fitted):
        again = rank_comments([fitted], seen, scenes, settings)
        if again is not None:
            choice = again
            if fitted not in pool:
                pool = [fitted, *pool]
    return _pack(
        choice,
        concern=concern,
        seen=seen,
        context=context,
        candidates=pool,
        stages=stages,
    )
