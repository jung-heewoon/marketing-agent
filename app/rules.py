from __future__ import annotations

import re
from datetime import datetime, timezone

from app.config import Settings

_LINK = re.compile(r"(https?://|www\.|youtu\.be\b|\b[a-z0-9.-]+\.(?:com|kr|net|me)\b)", re.I)
_HASHTAG = re.compile(r"#\S")
_FAKE_REVIEW = re.compile(
    r"운명원(?:에서|을|은|이)?\s*(?:직접\s*)?(?:봤|받아\s*봤|받아봤|해봤|해\s*봤|가봤|가\s*봤|써봤|써\s*봤|상담|이용했|결제)"
    r"|(?:봤는데|받아봤는데|해보니|써보니|가보니).{0,16}운명원"
    r"|내가\s*운명원"
    r"|운명원\s*덕분에"
    r"|(?:친구|언니|오빠|지인|엄마|아빠|동생|선배|후배).{0,20}(?:괜찮|추천|잘\s*맞|맞았|해\s*보|써\s*보|가\s*보)"
    r"|결과(?:를|가).{0,10}(?:들|봤)|다녀\s*왔|보고\s*왔|받아\s*왔|이미\s*(?:봤|받았)"
    r"|운명원.{0,24}들은\s*말"
)
_REPORT_STYLE = re.compile(
    r"(?:라는\s*말에\s*공감한다|에\s*공감한다\.|라고\s*생각한다|가\s*인상적이다|소문이\s*궁금하다)"
)
_HANJA = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]|[\U00020000-\U0002EBEF]")
_MIDDLE_DOT = re.compile(r"[·・•∙･]")
_BRAND_PAIR = re.compile(r"연애\s*(?:랑|와|과|하고|이랑|및|,)?\s*인연|인연\s*(?:랑|와|과|하고|이랑|및|,)?\s*연애")
_STOCK_BRIDGE = re.compile(
    r"내\s*짝.{0,12}(?:있는지|있나|알고\s*싶|보고\s*싶)|짝(?:이나|이)\s*있는지"
)
_LOOSE_BRAND = re.compile(r"소문|보이더라|보이네요|고민\s*중|말에\s*찔")
_COMMA_BRAND = re.compile(r",\s*운명원")
_BRAND_SAJU = re.compile(r"운명원.{0,6}(?:연애사주|인연사주)")
_BRAND_SUBJECT = re.compile(r"운명원(?:은|이)\s+(?!연애사주|인연사주)")
_WANT_TO_LOOK = re.compile(r"보고\s*싶|알고\s*싶|볼까|봐볼|궁금|끌리|본다던|본다길|본다는|봐준다던|물어\s*보|뭐라고\s*할")
_NEGATED_WANT = re.compile(
    r"(?:안|못)\s*(?:보고\s*싶|알고\s*싶|궁금|볼까|봐볼|끌리)"
    r"|(?:보고\s*싶|알고\s*싶|궁금|볼까|봐볼|끌리|본다던|본다길|본다는).{0,16}(?:않|안|없)"
)
_ECHO = re.compile(r"다는\s*말|셋째|세\s*호흡|말투\s*참고|같은\s*끝")
_FORMAL_COPY = re.compile(r"(?:습니다|입니다)\s*[,.]")
_DANGLING_TAG = re.compile(r",\s*(?:인연사주|연애사주|운명원)\s*[.!]?\s*$")
_STOPWORDS = {"진짜", "정말", "너무", "그냥", "사람", "생각", "마음", "영상", "이거", "저거", "이런", "그런"}
_GROUND_STOP = _STOPWORDS | {"연락", "많은", "없는", "있는", "하는", "하면", "오늘"}


class Reject(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def comment_length(text: str) -> int:
    return len((text or "").strip())


def _compact(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text or "")


def _content_tokens(text: str) -> list[str]:
    words = re.findall(r"[가-힣A-Za-z0-9]{2,}", text or "")
    tokens: list[str] = []
    for word in words:
        stem = re.sub(r"(은|는|이|가|을|를|에|에서|으로|로|과|와|도|만|이라|라|다는|라는|한다|합니다|해요)$", "", word)
        target = stem if len(stem) >= 2 else word
        if len(target) >= 2 and target not in _STOPWORDS:
            tokens.append(target)
    return tokens


def evidence_in_source(evidence: str, source: str) -> bool:
    core = _compact(evidence)
    src = _compact(source)
    if len(core) < 4 or not src:
        return False
    if core in src:
        return True
    tokens = _content_tokens(evidence)
    if not tokens:
        return False
    matched = sum(1 for token in tokens if token in src)
    return matched >= max(1, (len(tokens) + 1) // 2)


def find_evidence(comment: str, source: str) -> str:
    """영상 자료의 한 문장과 흔한 말이 아닌 핵심어가 두 개 이상 겹칠 때만 그 문장을 돌려준다."""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?。…])\s+|\n+", source or "") if part.strip()]
    best = ""
    best_score = 1
    for sentence in sentences:
        hits: list[str] = []
        for token in _content_tokens(sentence):
            if token in _GROUND_STOP or token in hits:
                continue
            if token in (comment or "") or _token_echo(token, comment):
                hits.append(token)
        if len(hits) > best_score:
            best_score = len(hits)
            best = sentence[:90]
    return best


def _token_echo(token: str, comment: str) -> bool:
    if len(token) < 2:
        return False
    for other in _content_tokens(comment):
        if other in _GROUND_STOP:
            continue
        if token[:2] == other[:2]:
            return True
    return False


def comment_uses_evidence(comment: str, evidence: str) -> bool:
    core = _compact(evidence)
    body = _compact(comment)
    if len(core) < 4 or not body:
        return False
    window = min(4, len(core))
    for index in range(0, len(core) - window + 1):
        if core[index:index + window] in body:
            return True
    for token in _content_tokens(evidence):
        if token in body or token[:2] in body:
            return True
    return False


def expresses_curiosity(text: str) -> bool:
    raw = text or ""
    if not _WANT_TO_LOOK.search(raw):
        return False
    return _NEGATED_WANT.search(raw) is None


def instruction_echo(text: str) -> bool:
    raw = (text or "").strip()
    tail = re.sub(r"[\s.!?~…]+$", "", raw)
    if re.search(r"말(?:이다)?$", tail):
        return True
    return bool(_ECHO.search(raw))


def brand_is_subject(text: str) -> bool:
    """운명원을 영상 문장의 주어로 쓴 경우. 사주집 이름이 아니다."""
    return _BRAND_SUBJECT.search(text or "") is not None


def brand_names_saju(text: str) -> bool:
    return _BRAND_SAJU.search(text or "") is not None


def hard_errors(comment: str, settings: Settings) -> list[str]:
    """게시를 막는 조건. 과제 규칙, 한자, 운명원이 사주집인 경우만 본다."""
    text = (comment or "").strip()
    errors: list[str] = []
    length = comment_length(text)
    if length < settings.min_chars or length > settings.max_chars:
        errors.append(f"댓글은 {settings.min_chars}~{settings.max_chars}자여야 합니다. 지금은 {length}자입니다.")
    if settings.brand not in text:
        errors.append(f"{settings.brand}이 댓글에 없습니다.")
    elif brand_is_subject(text):
        errors.append("운명원을 사람처럼 쓰지 마세요.")
    elif not expresses_curiosity(text):
        errors.append("아직 안 본 사주가 궁금한 말이 있어야 합니다.")
    if _LINK.search(text):
        errors.append("링크는 넣을 수 없습니다.")
    if _HASHTAG.search(text):
        errors.append("해시태그는 넣을 수 없습니다.")
    if _FAKE_REVIEW.search(text):
        errors.append("운명원을 이미 이용해 본 후기처럼 쓰면 안 됩니다.")
    if _HANJA.search(text):
        errors.append("한자가 섞여 있습니다. 한자를 전부 빼고 그 뜻을 한글로만 다시 쓰세요.")
    return errors


def style_notes(comment: str, settings: Settings) -> list[str]:
    """말투 메모. 고르기 점수와 화면 경고에만 쓰고 게시는 막지 않는다."""
    text = (comment or "").strip()
    notes: list[str] = []
    if _REPORT_STYLE.search(text):
        notes.append("독후감이나 번역투 어미(~에 공감한다, ~라고 생각한다 등)를 빼고 실제 유튜브 시청자 구어체(~네요, ~더라고요, ~듯, ~싶다)로 다시 쓰세요.")
    if _MIDDLE_DOT.search(text) or _BRAND_PAIR.search(text):
        notes.append("가운뎃점과 연애와 인연을 나란히 붙인 말은 빼세요. 연애사주 또는 인연사주 중 하나만 쓰세요.")
    if _STOCK_BRIDGE.search(text):
        notes.append("같은 결말로 고정되어 있습니다. 이번 영상 고민으로 다시 쓰세요.")
    if _FORMAL_COPY.search(text) or _DANGLING_TAG.search(text):
        notes.append("영상 문장을 받아 적거나 쉼표 뒤에 단어만 붙이지 마세요. 시청자 말투로 운명원을 문장 안에 넣으세요.")
    if _LOOSE_BRAND.search(text) or _COMMA_BRAND.search(text):
        notes.append("쉼표 뒤에 운명원을 붙이거나 소문, 보이더라로 끝내지 마세요.")
    echoed = instruction_echo(text)
    if echoed:
        notes.append("끝 문장이 설명문이다. 시청자가 입 밖으로 하는 말로 다시 써라.")
    return notes


def validate_comment(comment: str, source: str, evidence: str, settings: Settings) -> list[str]:
    text = (comment or "").strip()
    errors = hard_errors(comment, settings)
    errors.extend(style_notes(comment, settings))
    if not (evidence or "").strip():
        errors.append("영상 속 말을 두 단어 이상 짚지 않았습니다.")
    elif not evidence_in_source(evidence, source):
        errors.append("근거 문장이 영상 자료에 없습니다. 요약·자막·설명에 있는 핵심 표현을 고르세요.")
    elif not comment_uses_evidence(text, evidence):
        errors.append("댓글이 영상 속 핵심 키워드를 담지 못했습니다. 근거 구절의 핵심 단어를 자연스럽게 녹이세요.")
    return errors


def video_allowed(video_id: str, channel_id: str, settings: Settings) -> bool:
    if video_id in settings.test_video_ids:
        return True
    return channel_id in settings.channel_ids


def _euro(text: str) -> str:
    """댓글 끝소리에 맞춰 으로/로를 고른다. 받침이 없거나 ㄹ이면 로."""
    for ch in reversed((text or "").strip()):
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            jong = (code - 0xAC00) % 28
            if jong == 0 or jong == 8:
                return "로"
            return "으로"
    return "로"


def post_sentence(
    profile_label: str,
    channel_name: str,
    video_index: int | None,
    comment: str,
    *,
    title: str = "",
    status: str = "success",
    reason: str = "",
) -> str:
    channel = (channel_name or "").strip() or "채널"
    target = f"{channel}의 {video_index}번 영상" if video_index else f"{channel}의 영상"
    if title.strip():
        target = f"{target} 「{title.strip()}」"
    body = (comment or "").strip()
    sentence = f"{profile_label}의 프로필이 {target}에 댓글을 {body}{_euro(body)} "
    if status == "success":
        return sentence + "남겼습니다."
    why = f" {reason.strip()}" if reason.strip() else ""
    return sentence + f"남기지 못했습니다.{why}"


def assert_can_post(
    *,
    video_id: str,
    channel_id: str,
    comment: str,
    source: str,
    evidence: str,
    settings: Settings,
    success_today: int,
    already_posted: bool,
) -> None:
    """허용 채널, 영상당 1개, 하루 상한, 필수 문장 규칙만 막는다.

    영상 속 말 언급은 source, evidence 로 받지만 게시 조건이 아니다.
    한 영상에는 계정을 가리지 않고 성공 댓글 하나만 남긴다.
    """
    if not video_allowed(video_id, channel_id, settings):
        raise Reject("허용된 채널의 영상이 아닙니다. 지정 채널 5개와 본인 테스트 영상만 게시할 수 있습니다.")
    if already_posted:
        raise Reject("이 영상에는 이미 댓글을 남겼습니다. 영상당 1개만 게시할 수 있습니다.")
    if success_today >= settings.daily_max_comments:
        raise Reject(f"하루 게시 상한 {settings.daily_max_comments}개에 도달했습니다.")
    errors = hard_errors(comment, settings)
    if errors:
        raise Reject(" ".join(errors))


def views_per_hour(view_count: int, published_at: datetime, now: datetime) -> float:
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    hours = max((now - published_at).total_seconds() / 3600, 1 / 60)
    return view_count / hours


def is_rising(
    view_count: int | None,
    published_at: datetime | None,
    now: datetime,
    *,
    min_views: int = 1000,
    max_views: int = 5000,
    min_per_hour: float = 150,
) -> bool:
    if view_count is None or published_at is None:
        return False
    if not min_views <= view_count <= max_views:
        return False
    return views_per_hour(view_count, published_at, now) >= min_per_hour
