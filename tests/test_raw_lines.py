import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.lines import parse_comment_lines
from app.llm import (
    build_messages,
    build_read_messages,
    build_repair_messages,
    build_weave_messages,
    join_comment,
    without_brand_as_person,
    keeps_viewer,
    load_brand_phrases,
    parse_reading,
    pick_viewer,
    rank_comments,
)
from app.laya_judge import missing_laya_note
from app.rules import _FAKE_REVIEW, expresses_curiosity
from app.youtube import VideoContext


def test_writer_prompt_has_no_brand_and_shows_real_comments():
    from app.config import load_settings

    context = VideoContext(
        transcript="서운함",
        comments=["진짜 공감되네요"],
        description="연락이 없으면 하루가 망가진다",
        source_text="서운함 많은 여자",
        source_kind="transcript",
        excerpt="서운함 많은 여자들이 꼭 알아야 할 것",
        summary="서운함이 많은 사람 이야기",
    )
    settings = load_settings()
    read_blob = "\n".join(item["content"] for item in build_read_messages({"title": "서운함"}, context))
    assert "고민:" in read_blob
    assert "장면:" in read_blob
    assert "시청자:" in read_blob
    assert "진짜 공감되네요" in read_blob
    assert context.excerpt in read_blob
    blob = "\n".join(
        item["content"]
        for item in build_messages(
            {"title": "서운함"},
            context,
            settings,
            persona="20대 후반 여성, 연락이 뜸하다",
            scenes=["서운함 많은 여자들이 꼭 알아야 할 것"],
            concern="답장이 없으면 내가 부족한가 싶다",
        )
    )
    for banned in ("운명원", "연애사주", "인연사주", "ㅇㅇ사주", "잘 본다던데"):
        assert banned not in blob
    assert "너는 20대 후반 여성" in blob
    assert "방금 봤다" in blob
    assert "진짜 공감되네요" in blob
    assert context.excerpt in blob
    assert "답장이 없으면 내가 부족한가 싶다" not in blob
    assert "알다가도 모르겠" not in blob
    assert "차이기만 했던 1인" not in blob
    assert "유튜브에 달" in blob
    assert "위에 달린 댓글 말투" in blob
    assert "첫 마디" not in blob
    from app.voice import load_voice

    stored = load_voice()
    if stored:
        assert stored[0]["text"] not in blob


def test_reading_keeps_only_scenes_that_are_in_the_video():
    from tests.test_post_gate import SOURCE

    raw = (
        "고민: 답장이 없으면 내가 부족한가 싶다\n"
        "장면: 서운함 많은 여자들이 꼭 알아야 할 것\n"
        "장면: 영상에 없는 장면이다\n"
        "시청자: 20대 여성, 연락이 뜸한 연애\n"
    )
    parsed = parse_reading(raw, SOURCE)
    assert "답장" in parsed["concern"]
    assert parsed["scenes"] == ["서운함 많은 여자들이 꼭 알아야 할 것"]
    assert "20대" in parsed["persona"]
    titled = parse_reading("고민: 서운함은 기대에서 비롯됨을 설명합니다.", SOURCE)
    assert titled["concern"] == ""


def test_viewer_line_goes_to_the_weave_with_examples_not_a_phrase_list():
    from app.config import load_settings
    from tests.test_post_gate import SOURCE

    viewer = "서운함 많은 여자라 답장 하나에 멘탈이 나간다"
    lines = parse_comment_lines(
        "고민: 좋아하는 사람 앞에서 말이 안 나온다\n"
        "오늘은 그냥 쉬고 싶다\n"
        f"{viewer}\n"
        "서운함은 기대에서 비롯됨을 설명합니다.\n"
    )
    picked = pick_viewer(lines, SOURCE, ["서운함 많은 여자들이 꼭 알아야 할 것"], load_settings().brand)
    assert picked == viewer
    concern = "좋아하는 사람 앞에서 말이 안 나온다"
    messages = build_weave_messages(picked, concern)
    roles = [item["role"] for item in messages]
    assert roles[0] == "system"
    assert roles.count("assistant") >= 3
    blob = "\n".join(item["content"] for item in messages)
    assert viewer in blob
    assert concern not in blob
    assert "[고민]" not in blob
    assert "인연사주를 본다" in blob
    assert "안 봤다" in messages[0]["content"]
    assert "가봤" not in messages[0]["content"]
    assert "인연으로 잇는다" in messages[0]["content"]
    assert "운명원은" not in messages[0]["content"]
    assert "운명원에서" in blob
    assert "저런 사람" in blob
    assert "인연은" in blob
    assert "인연사주" in blob
    assert "한 문장" not in blob
    assert "보고 싶다는" not in blob
    assert "본다던데" in blob
    assert "해볼까" in blob
    assistant = "\n".join(item["content"] for item in messages if item["role"] == "assistant")
    assert "가볼" not in assistant
    assert "연락 기다리는" not in assistant
    assert "답장" not in assistant
    assert "잘난 척" not in assistant
    assert "같은 사람만" not in assistant
    assert "저도 겪어봤거든요" not in assistant
    assert "봐주면" not in assistant
    assert "명확" not in assistant
    assert "운명원" in assistant.splitlines()[0]
    assert "문장 중간에" not in blob
    assert "콕 집어준다던데" not in blob
    assert "이미 쓴 표현" not in blob
    assert "끌린다던데" not in blob


def test_phrase_list_is_curiosity_without_a_review():
    phrases = load_brand_phrases()
    assert 4 <= len([line for line in phrases if "연애사주" in line]) <= 12
    assert 4 <= len([line for line in phrases if "인연사주" in line]) <= 12
    for line in phrases:
        assert "운명원" in line
        assert not line.startswith(("이런 거", "이 고민", "이 마음", "이런 부분"))
        assert ("연애사주" in line) != ("인연사주" in line)
        assert expresses_curiosity(line)
        assert _FAKE_REVIEW.search(line) is None
        for banned in ("소문", "보이더라", "내 짝", "·"):
            assert banned not in line


def test_fresh_retry_keeps_the_first_clean_draft():
    from app.llm import Ranked, _take_attempt

    dirty = Ranked("짧은 실패", ["아직 안 본 사주가 궁금한 말이 있어야 합니다."], [], 1, "")
    clean = Ranked("규칙을 통과한 댓글입니다. 운명원에서 그런 사람 인연은 잘 본다던데 한번 해볼까.", [], [], 4, "")
    assert _take_attempt(dirty, None) is dirty
    assert _take_attempt(clean, dirty) is clean
    assert _take_attempt(dirty, clean) is clean


def test_followup_stays_behind_the_viewer_comment():
    from app.llm import claims_result, followup_sentence, hearsay

    viewer = "사람 앞에서 분위기 잡는 게 아직 어색해요"
    tail = "운명원 연애사주가 잘 본다던데, 나도 한번 보고 싶어요."
    whole = join_comment(viewer, tail)
    assert whole.startswith(viewer + " ")
    assert whole.index("운명원") > len(viewer)
    glued = "분위기 만드는 행동까지 운명원 연애사주에서 봐주면 내가 어떻게 해야 할지 더 명확해져요."
    piece = followup_sentence(glued, viewer)
    assert piece.startswith("운명원")
    assert "분위기" not in piece
    kept = followup_sentence("근데 연락 기다리는 것도 운명원에서 잘 본다던데", viewer)
    assert kept.startswith("근데")
    assert "연락" in kept
    assert claims_result(glued)
    assert claims_result(piece)
    assert not claims_result(tail)
    assert not claims_result("운명원에서 저런 사람 인연은 잘 봐준다던데")
    assert hearsay(tail)
    instruction = "운명원 연애사주, 보고 싶다는 한 문장."
    assert not hearsay(instruction)


def test_long_comment_keeps_the_breath_before_the_brand():
    from app.llm import fit_comment, one_breath

    long = (
        "ㅋㅋ 진짜... 연락 안 하면 그냥 편했던 건데, 막상 내가 없으니까 안정 기지가 사라진 걸 뒤늦게 깨닫는 거 맞아요. "
        "근데 그 '심심해서' 연락하는 것까지 구분하기는 너무 어려워요 ㅠ "
        "미련인 줄 알았는데 그냥 빈자리 채우기인 거면 진짜 헷갈려요. "
        "운명원에서 미련인지 빈자리 채우기인지 구분하는 것도 본다던데, 나도 한번 가볼까."
    )
    fitted = fit_comment(long, 120)
    assert 40 <= len(fitted) <= 120
    assert "운명원" in fitted
    assert "미련" in fitted
    assert "안정 기지" not in fitted
    assert fitted in long.replace("  ", " ")
    short = "연락 기다리는 건 진짜 별로예요. 운명원에서 연락 기다리는 것도 잘 본다던데."
    assert fit_comment(short, 120) == short
    head = long.split("운명원")[0].strip()
    breath = one_breath(head, 120)
    assert len(breath) < len(head)
    assert breath in head


def test_copied_video_comment_is_dropped():
    shown = "ㅠㅠ분명 뚝딱거리고 티나는데 안다가옴"
    copied = "ㅠㅠ분명 뚝딱거리고 티나는데... 안다가옴...."
    own = "아 근데 연락 기다리는 건 진짜 별로예요. 저도 한참 그랬거든요."
    picked = pick_viewer([copied, own], "분위기 이야기", [], "운명원", shown=[shown])
    assert picked == own


def test_empty_comments_use_the_summary():
    from app.config import load_settings

    context = VideoContext(
        transcript="",
        comments=[],
        description="",
        source_text="남자 연락을 기다리는 이야기",
        source_kind="livewiki",
        excerpt="",
        summary="남자 연락을 기다리는 이야기",
    )
    blob = "\n".join(
        item["content"] for item in build_messages({"title": "기다림"}, context, load_settings())
    )
    assert "댓글이 없다" in blob
    assert "남자 연락을 기다리는 이야기" in blob
    assert "요약:" in blob
    assert "유튜브에 다는 말투" in blob


def test_copied_assignment_example_is_dropped():
    copied = "근데 진짜 남자심리는 알다가도 모르겠다...ㅠㅠ 맨날 연애하면 차이기만 했던 1인."
    own = "아 근데 연락 기다리는 건 진짜 별로예요. 저도 한참 그랬거든요."
    picked = pick_viewer([copied, own], "분위기 이야기", [], "운명원")
    assert picked == own


def test_my_own_line_beats_agreeing_with_the_video():
    own = "아 근데 연락 기다리는 건 진짜 별로예요. 저도 한참 그랬거든요."
    agree = "남자 심리보다 내 하루를 온전히 즐기는 게 먼저라는 말, 너무 공감돼요."
    picked = pick_viewer(
        [agree, own],
        "서운함 많은 여자",
        ["내 하루를 온전히 즐기는 게 먼저"],
        "운명원",
    )
    assert picked == own


def test_brand_spoken_as_a_person_is_not_shown():
    bad = "운명원은 남자심리 알기보다 그냥 인연이면 만나는 거라고 믿는 차이기만 했던 사람입니다."
    viewer = "아 근데 남자심리는 진짜 모르겠다. 맨날 차이기만 했던 1인이에요."
    good = "아 근데 차이기만 했던 나는 운명원 인연사주에서 언제 만나는지 봐볼까 해요."
    assert without_brand_as_person([bad, good], viewer) == [good]
    assert without_brand_as_person([bad], viewer) == [viewer]
    repair = build_repair_messages(bad, ["운명원 연애사주 또는 운명원 인연사주라고 쓰세요."])
    assert repair[1]["role"] == "user"
    assert repair[1]["content"].startswith("운명원은")
    assert "운명원에서" in repair[2]["content"]
    assert "본다던데" in repair[2]["content"]
    assert "해볼까" in repair[2]["content"]
    assert "가볼" not in repair[2]["content"]
    assert "사람입니다" not in repair[2]["content"]
    assert "운명원은" not in repair[0]["content"]


def test_short_brand_line_does_not_replace_the_viewer():
    from app.config import load_settings
    from tests.test_post_gate import SOURCE

    viewer = "서운함 많은 여자라 답장 하나에 멘탈이 나간다"
    short = "이 마음 운명원 연애사주가 끌린다던데 봐볼까 싶어요"
    linked = f"{viewer}. 운명원 연애사주에서 잘 알려준다던데 봐볼까 싶어요"
    assert keeps_viewer(short, viewer) is False
    assert keeps_viewer(linked, viewer) is True
    picked = rank_comments([short, linked], SOURCE, ["서운함 많은 여자"], load_settings())
    assert picked is not None
    assert viewer in picked.comment
    assert "운명원" in picked.comment


def test_weave_without_brand_ranks_below_a_linked_line():
    from app.config import load_settings
    from tests.test_post_gate import SOURCE

    viewer = "서운함 많은 여자라 답장 하나에 멘탈이 나간다"
    plain = "오늘은 그냥 쉬고 싶다"
    linked = f"{viewer}. 운명원 연애사주가 잘 본다던데 나도 봐볼까 해요."
    picked = rank_comments([plain, linked], SOURCE, [], load_settings())
    assert picked is not None
    assert picked.comment == linked
    assert "운명원" in picked.comment
    assert picked.hard == []


def test_candidates_stay_when_the_comment_is_edited(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKETING_AGENT_DB", str(tmp_path / "agent.db"))
    db.init_db()
    db.save_draft(
        {
            "video_id": "rawvideo001",
            "concern": "",
            "comment": "",
            "evidence": "",
            "source_text": "서운함 많은 여자들이 꼭 알아야 할 것",
            "source_kind": "transcript",
            "warnings": "원문입니다. 아직 게시 검사가 없습니다.",
            "candidates": ["원문 한 줄", "다른 줄"],
        }
    )
    db.save_draft_comment("rawvideo001", "고친 문장")
    row = db.latest_draft("rawvideo001")
    assert json.loads(row["candidates"]) == ["원문 한 줄", "다른 줄"]
    assert row["comment"] == "고친 문장"


def test_raw_line_without_brand_is_rejected(client):
    from app.config import load_settings

    settings = load_settings()
    channel = settings.channels[0].id
    from tests.test_post_gate import _video

    _video("rawpost0001", channel)
    db.save_draft(
        {
            "video_id": "rawpost0001",
            "concern": "",
            "comment": "",
            "evidence": "",
            "source_text": "서운함 많은 여자들이 꼭 알아야 할 것. 답장 하나에 멘탈이 나간다.",
            "source_kind": "transcript",
            "warnings": "",
            "candidates": ["서운함 많은 여자라 답장 하나에 멘탈이 나간다. 분석은 그만하고 싶다."],
        }
    )
    response = client.post(
        "/api/posts",
        json={
            "video_id": "rawpost0001",
            "profile_id": "youtube1",
            "comment": "서운함 많은 여자라 답장 하나에 멘탈이 나간다. 분석은 그만하고 싶다.",
        },
    )
    assert response.status_code == 400
    assert "운명원" in response.json()["detail"]


def test_edited_comment_is_rechecked(client):
    from app.config import load_settings
    from tests.test_post_gate import GOOD, SOURCE, _video

    settings = load_settings()
    channel = settings.channels[0].id
    _video("editpost001", channel)
    db.save_draft(
        {
            "video_id": "editpost001",
            "concern": "",
            "comment": "원문",
            "evidence": "",
            "source_text": SOURCE,
            "source_kind": "title",
            "warnings": "",
            "candidates": ["원문"],
        }
    )
    response = client.post(
        "/api/posts",
        json={
            "video_id": "editpost001",
            "profile_id": "youtube1",
            "comment": "재회하고 싶은데 답장이 늦으면 운명원 연애사주 보고 싶다",
        },
    )
    assert response.status_code == 400
    assert "자여야" in response.json()["detail"]
    review = client.post(
        "/api/posts",
        json={
            "video_id": "editpost001",
            "profile_id": "youtube1",
            "comment": "서운함 많은 여자라 답장 하나에 멘탈이 나간다. 내가 운명원 직접 봤는데 잘 맞았다.",
        },
    )
    assert review.status_code == 400
    assert "후기" in review.json()["detail"]


def test_missing_laya_is_not_called_validated():
    note = missing_laya_note(None)
    assert "미실행" in note
    assert "검증 완료" not in note
    assert missing_laya_note(object()) == ""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKETING_AGENT_DB", str(tmp_path / "agent.db"))
    from app import main as main_module

    with TestClient(main_module.app) as test_client:
        yield test_client
