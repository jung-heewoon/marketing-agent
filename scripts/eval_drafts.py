"""과제 예시 영상 다섯 개의 단계별 초안을 data/eval 에 남긴다.

config.yaml 의 llm.provider 를 쓴다. 로컬 모델과 Gemini 를 비교하려면
provider 를 바꾼 뒤 같은 명령을 다시 실행한다.

    .venv\\Scripts\\python -m scripts.eval_drafts
"""

from __future__ import annotations

import json
import traceback

from app.config import ROOT, load_settings
from app.llm import ModelError, active_model_name, generate_draft
from app.youtube import collect_context, fetch_player

EXAMPLE_VIDEOS = (
    "akn-AvCeHBI",
    "JjY09DSdzx0",
    "DvxMfC_KjX0",
    "3K82-PjUeP0",
    "WxXINTXQSd4",
)


def evaluate() -> None:
    settings = load_settings()
    out_dir = ROOT / "data" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    for video_id in EXAMPLE_VIDEOS:
        print(video_id, flush=True)
        path = out_dir / f"{settings.llm_provider}-{video_id}.json"
        record: dict = {"video_id": video_id, "provider": settings.llm_provider, "model": active_model_name(settings)}
        try:
            player = fetch_player(video_id)
            video = {
                "video_id": video_id,
                "title": player.get("title") or video_id,
                "description": player.get("description") or "",
                "channel_id": player.get("channel_id") or "",
                "channel_name": player.get("channel_name") or "",
                "url": player.get("url") or f"https://www.youtube.com/watch?v={video_id}",
            }
            context = collect_context(video, settings)
            draft = generate_draft(video, context, settings, on_status=print)
            record.update(
                {
                    "title": video["title"],
                    "concern": draft.get("concern") or "",
                    "comment": draft.get("comment") or "",
                    "evidence": draft.get("evidence") or "",
                    "warnings": draft.get("warnings") or [],
                    "candidates": draft.get("candidates") or [],
                    "source_kind": draft.get("source_kind") or "",
                    "stages": draft.get("stages") or {},
                }
            )
            print(record["comment"], flush=True)
        except (ModelError, Exception) as exc:
            record["error"] = str(exc)
            record["trace"] = traceback.format_exc()
            print(record["error"], flush=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    evaluate()


if __name__ == "__main__":
    main()
