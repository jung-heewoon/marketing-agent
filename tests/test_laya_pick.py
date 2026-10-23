from dataclasses import replace

from app.config import load_settings
from app.llm import completion_target, keeps_viewer, rank_comments
from app.rules import hard_errors, validate_comment


SOURCE = "서운함 많은 여자들이 꼭 알아야 할 것. 연락이 없으면 하루가 망가진다."
EVIDENCE = "서운함 많은 여자"
ASIDE = "서운함 많은 여자 얘기 딱 나인데 연락 한통에 하루가 날아가네요. 운명원 연애사주 한번 보고 싶어요"
MAIN = "서운함 많은 여자라 맨날 같은 패턴만 반복되네요. 그 이유를 운명원 인연사주로 봐볼까 싶어요"
SCENE = "서운함 많은 여자라 답장 하나에 멘탈이 나간다. 운명원 연애사주가 잘 본다던데 나도 봐볼까 해요."
LOOSE = "연락이 없는 하루가 너무 길다. 운명원 연애사주나 한번 보고 싶다 정말."


class FakeLaya:
    def __init__(self, table):
        self.table = table

    def predict(self, state, questions):
        human, pull, video, confidence = self.table[state["candidate_sentence"]]
        return {
            "answers": {
                "human": {"choice": human, "confidence": confidence},
                "pull": {"choice": pull, "confidence": confidence},
                "video": {"choice": video, "confidence": confidence},
            }
        }


def test_comments_pass_the_hard_rules():
    settings = load_settings()
    for comment in (ASIDE, MAIN):
        assert hard_errors(comment, settings) == []
        assert validate_comment(comment, SOURCE, EVIDENCE, settings) == []


def test_opener_beats_the_same_line_without_one():
    settings = load_settings()
    plain = "나도 서운함 많은 날이면 답장 하나에 멘탈이 나가요. 운명원 연애사주 한번 봐볼까 해요."
    seasoned = "아 근데 나도 서운함 많은 날이면 답장 하나에 멘탈이 나가요. 운명원 연애사주 한번 봐볼까 해요."
    picked = rank_comments([plain, seasoned], SOURCE, ["서운함 많은 여자"], settings)
    assert picked is not None
    assert picked.comment == seasoned
    assert picked.hard == []


def test_personal_line_beats_a_paraphrase():
    settings = load_settings()
    scene = "연애를 잘하고 싶으면 연애 말고 소중한 다른 걸 하나 만드셔야 돼요"
    copied = "연애 말고 다른 걸 만드는 게 정답인 것 같아, 나만 좋아해 줄 사람 만나면 편해져서 운명원 연애사주가 끌린다던데 봐볼까 싶어요."
    personal = "나도 연애만 붙잡고 살았네요. 다른 걸 하나 만들어야 한다니까 찔려서, 운명원 연애사주에서는 이 고집을 뭐라고 할까 궁금하다가 또 그 생각만 해요."
    assert hard_errors(copied, settings) == []
    assert hard_errors(personal, settings) == []
    picked = rank_comments([copied, personal], scene, [scene], settings)
    assert picked is not None
    assert picked.comment == personal


def test_scene_line_beats_a_loose_line():
    settings = load_settings()
    picked = rank_comments([LOOSE, SCENE], SOURCE, ["서운함 많은 여자들이 꼭 알아야 할 것"], settings)
    assert picked is not None
    assert picked.comment == SCENE
    assert picked.hard == []


def test_tail_only_line_is_not_the_viewer():
    viewer = "서운함 많은 여자라 답장 하나에 멘탈이 나간다"
    tail = "이 마음 운명원 연애사주가 끌린다던데 봐볼까 싶어요"
    assert keeps_viewer(tail, viewer) is False
    assert keeps_viewer(f"{viewer}. 운명원 연애사주 한번 보고 싶어요", viewer) is True


def test_laya_bonus_picks_the_human_line():
    settings = load_settings()
    plain = "나도 서운함 많은 날이면 답장 하나에 멘탈이 나가요. 운명원 인연사주로 이 고집을 봐볼까 하다가 또 생각나요."
    human = "나도 서운함 많은 날이면 연락 하나에 멘탈이 나가요. 운명원 연애사주로 이 고집을 봐볼까 하다가 또 생각나요."
    agent = FakeLaya({
        plain: ("NO", "NO", "NO", 0.2),
        human: ("YES", "YES", "YES", 0.9),
    })
    picked = rank_comments(
        [plain, human],
        SOURCE,
        ["서운함 많은 여자들이 꼭 알아야 할 것"],
        settings,
        agent=agent,
    )
    assert picked is not None
    assert picked.comment == human
    assert picked.hard == []


def test_gemini_target_drops_local_reasoning():
    settings = load_settings()
    assert settings.llm_provider == "strata"
    _url, headers, extra = completion_target(settings)
    assert extra.get("reasoning_effort") == "none"
    assert "Authorization" not in headers
    gemini = replace(settings, llm_provider="gemini", gemini_api_key="test-key", gemini_model="gemini-2.5-flash")
    url, headers, extra = completion_target(gemini)
    assert url.endswith("/chat/completions")
    assert headers["Authorization"] == "Bearer test-key"
    assert "reasoning_effort" not in extra
