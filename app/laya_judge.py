from __future__ import annotations

from app.config import Settings

_QUESTIONS = {
    "human": {
        "type": "choice",
        "instructions": (
            "candidate_sentence가 한국 유튜브에 사람이 단 댓글처럼 들리는가. "
            "보고서, 번역투, 브랜드 광고 카피는 NO."
        ),
        "criteria": {
            "YES": "영상 보고 짧게 남긴 사람 말",
            "NO": "보고서, 번역투, 광고 카피",
        },
    },
    "pull": {
        "type": "choice",
        "instructions": (
            "이 댓글을 읽은 사람이 brand에서 연애사주나 인연사주를 보고 싶어지는가. "
            "영상 고민이 먼저 있고, 직접 본 후기 없이 궁금해서 한번 보고 싶다는 마음이 남으면 YES. "
            "브랜드가 쉼표 뒤에 의미 없이 붙거나, 없거나, 영상과 무관하면 NO."
        ),
        "criteria": {
            "YES": "영상 고민 다음에 그 사주가 궁금해서 보고 싶어진다",
            "NO": "브랜드가 의미 없이 붙었거나 궁금증이 없다",
        },
    },
    "video": {
        "type": "choice",
        "instructions": (
            "candidate_sentence가 video_text의 주제나 발언을 반영하는가. "
            "영상에 없는 장면을 지어내면 NO."
        ),
        "criteria": {
            "YES": "영상의 말이나 상황을 반영한다",
            "NO": "영상과 무관하거나 없는 장면을 지어냈다",
        },
    },
}

_agent = None
_load_error: str | None = None


def missing_laya_note(agent) -> str:
    if agent is not None:
        return ""
    return "Laya 미실행. 규칙만 확인했습니다."


def get_agent(settings: Settings):
    global _agent, _load_error
    if not settings.laya_enabled:
        return None, "Laya가 꺼져 있습니다."
    if _agent is not None:
        return _agent, None
    if _load_error is not None:
        return None, _load_error
    try:
        import laya
    except ImportError:
        _load_error = "laya 패키지가 없습니다. 규칙만 통과한 초안을 씁니다."
        return None, _load_error
    try:
        _agent = laya.load(
            settings.laya_repo,
            device=settings.laya_device,
            subfolder=settings.laya_subfolder or None,
        )
    except Exception as exc:
        _load_error = f"Laya를 불러오지 못했습니다. {exc}"
        return None, _load_error
    return _agent, None


def _ask(agent, comment: str, brand: str, video_text: str) -> tuple[str, str, str, float]:
    try:
        result = agent.predict(
            {"candidate_sentence": comment, "brand": brand, "video_text": video_text[:500]},
            _QUESTIONS,
        )
    except Exception:
        return "NO", "NO", "NO", 0.0
    answers = (result or {}).get("answers") or {}
    human = _choice(answers, "human", "NO")
    role = _choice(answers, "pull", "NO")
    video = _choice(answers, "video", "NO")
    confidence = min(_confidence(answers, "human"), _confidence(answers, "pull"), _confidence(answers, "video"))
    return human, role, video, confidence


def _choice(answers: dict, key: str, default: str) -> str:
    item = answers.get(key) or {}
    choice = item.get("choice") if isinstance(item, dict) else ""
    return str(choice or default)


def _confidence(answers: dict, key: str) -> float:
    item = answers.get(key) or {}
    try:
        return float(item.get("confidence") or 0)
    except (TypeError, ValueError):
        return 0.0
