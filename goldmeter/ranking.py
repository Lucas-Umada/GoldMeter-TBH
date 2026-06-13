"""Per-stage gold/min ranking, aggregated across sessions.

Every finished stage is one JSON line in sessions/session-*.jsonl (written
by the tracker). This module folds those records — plus the live records of
the running session — into one entry per stage and ranks them by gold/min,
so the most profitable stage to farm floats to the top.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .tracker import SESSIONS_DIR

# Stages glimpsed for less than this aren't rate-rankable: a 3-second
# misread window would otherwise produce absurd gold/min figures.
MIN_RANKABLE_SECONDS = 30.0


@dataclass
class StageStats:
    stage: str
    gold_earned: float = 0.0
    duration: float = 0.0
    visits: int = 0

    @property
    def gold_per_min(self) -> float:
        return (self.gold_earned / self.duration) * 60 if self.duration > 1 else 0.0


def load_session_records(
    sessions_dir: Path = SESSIONS_DIR, exclude: Path | None = None
) -> list[dict]:
    """All stage records from past session logs.

    `exclude` skips the running session's own file so its stages aren't
    counted twice when combined with the live tracker history.
    """
    records: list[dict] = []
    if not sessions_dir.is_dir():
        return records
    for path in sorted(sessions_dir.glob("session-*.jsonl")):
        if exclude is not None and path.resolve() == exclude.resolve():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                records.append(rec)
    return records


def aggregate(records: Iterable[Mapping]) -> dict[str, StageStats]:
    """Fold stage records into one StageStats per stage label.

    Records with a missing/empty stage or non-numeric fields are skipped —
    session files survive partial writes and hand-editing.
    """
    stats: dict[str, StageStats] = {}
    for rec in records:
        stage = rec.get("stage")
        gold = rec.get("gold_earned")
        duration = rec.get("duration_sec")
        if not stage or not isinstance(gold, (int, float)) \
                or not isinstance(duration, (int, float)) or duration < 0:
            continue
        entry = stats.setdefault(str(stage), StageStats(stage=str(stage)))
        entry.gold_earned += gold
        entry.duration += duration
        entry.visits += 1
    return stats


def rank(stats: dict[str, StageStats]) -> list[StageStats]:
    """Stages sorted by gold/min, best first.

    Stages with too little accumulated time sink to the bottom (ordered by
    gold earned) instead of ranking on a meaningless rate.
    """
    return sorted(
        stats.values(),
        key=lambda s: (
            s.duration >= MIN_RANKABLE_SECONDS,
            s.gold_per_min if s.duration >= MIN_RANKABLE_SECONDS else 0.0,
            s.gold_earned,
        ),
        reverse=True,
    )
