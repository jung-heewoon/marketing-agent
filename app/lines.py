from __future__ import annotations

import re

_BULLET = re.compile(r"^(?:\d+\s*[\.\)、:]|[-*•]|댓글\s*\d*\s*[:：])\s*")
_FENCE = re.compile(r"^```")
_RULE = re.compile(r"^[-=]{3,}$")
_RESIDUE = re.compile(r"사주집|한\s*번만\s*가리|영상에서\s*나온\s*말|이\s*댓글|가렸|가린다|가려서|가려라")


def parse_comment_lines(text: str) -> list[str]:
    raw = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    lines: list[str] = []
    for row in raw.splitlines():
        line = row.strip()
        if not line or _FENCE.match(line) or _RULE.match(line):
            continue
        line = _BULLET.sub("", line).strip().strip("\"'“”‘’")
        if not line or line in lines:
            continue
        lines.append(line)
    return lines


def instruction_residue(line: str) -> bool:
    """지시문을 댓글인 것처럼 베낀 줄."""
    return bool(_RESIDUE.search(line or ""))
