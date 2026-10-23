from app.config import load_settings
from app.lines import instruction_residue, parse_comment_lines
from app.llm import _error_message, build_messages, hold_without_transcript
from app.rules import find_evidence, validate_comment
from app.youtube import VideoContext


SOURCE = "서운함 많은 여자들이 꼭 알아야 할 것. 연락이 없으면 하루가 망가진다."


class _Response:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


def test_plain_lines_drop_numbers_quotes_rules_and_dupes():
    raw = """
1. "서운함 많은 여자라 하루가 길어요. 운명원 연애사주 보고 싶다"
---
2) 서운함 많은 여자라 하루가 길어요. 운명원 연애사주 보고 싶다
- 읽씹만 당해도 하루가 가네요. 운명원 인연사주 궁금해요

```
"""
    lines = parse_comment_lines(raw)
    assert lines == [
        "서운함 많은 여자라 하루가 길어요. 운명원 연애사주 보고 싶다",
        "읽씹만 당해도 하루가 가네요. 운명원 인연사주 궁금해요",
    ]
    assert parse_comment_lines("") == []
    assert parse_comment_lines("---\n\n```") == []


def test_invented_referral_is_rejected():
    settings = load_settings()
    referred = "서운함 많은 여자라 친구가 운명원 연애사주 괜찮다고 해서 나도 봐볼까 해요"
    assert any("후기" in error for error in validate_comment(referred, SOURCE, "서운함 많은", settings))
    heard = "서운함 많은 여자라 답장 하나에 멘탈이 나간다. 운명원 연애사주에서 들은 말이라고 생각합니다"
    assert any("후기" in error for error in validate_comment(heard, SOURCE, "서운함 많은", settings))


def test_grounding_needs_two_content_words():
    assert find_evidence("연락이 없으면 불안해요. 운명원 연애사주 보고 싶다", SOURCE) == ""
    hit = find_evidence("서운함 많은 여자라 하루가 길어요. 운명원 연애사주 보고 싶다", SOURCE)
    assert "서운함" in hit


def test_prompt_has_no_brand_before_the_weave():
    settings = load_settings()
    context = VideoContext(
        transcript="서운함 많은 여자",
        comments=["진짜 공감되네요"],
        description="연락이 없으면 하루가 망가진다",
        source_text=SOURCE,
        source_kind="transcript",
        excerpt="서운함 많은 여자들이 꼭 알아야 할 것",
        summary="서운함이 많은 사람 이야기",
    )
    messages = build_messages({"title": "서운함"}, context, settings)
    blob = "\n".join(item["content"] for item in messages)
    for banned in ("운명원", "ㅇㅇ사주", "연애사주", "인연사주", "잘 본다던데"):
        assert banned not in blob
    assert "진짜 공감되네요" in blob
    echo = "이 댓글이 영상에서 나온 말을 짚고, 사주집은 운명원 연애사주로 한 번만 가렸다."
    assert instruction_residue(echo)


def test_title_only_source_needs_review():
    ok, warnings = hold_without_transcript("title", "댓글", True, [])
    assert ok is False
    assert "검토 필요" in warnings[0]
    still_ok, same = hold_without_transcript("transcript", "댓글", True, [])
    assert still_ok is True
    assert same == []
    held, notes = hold_without_transcript("livewiki", "댓글", True, [])
    assert held is False
    assert "검토 필요" in notes[0]


def test_502_names_the_error_type():
    failed = _error_message(_Response(502, {"error": {"type": "structured_output_failed", "message": "bad json"}}))
    assert "structured_output_failed" in failed
    other = _error_message(_Response(502, {"error": {"type": "engine_died", "message": "down"}}))
    assert "서버 오류" in other
    assert "engine_died" in other
