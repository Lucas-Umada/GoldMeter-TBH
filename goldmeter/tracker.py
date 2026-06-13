"""Stage/gold tracking state machine.

Feeds on noisy OCR readings and produces per-stage statistics. Two defenses
against misreads:

- A changed stage label must be seen on 2 consecutive polls before the
  tracker accepts it as a real stage change.
- A gold value that jumps implausibly (relative to the last accepted value)
  must also repeat before it is accepted. Plausible increases are accepted
  immediately; gold earned is accumulated as the sum of positive deltas, so
  spending gold mid-stage doesn't corrupt the "earned" number.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

CONFIRM_READS = 2
# An increase beyond last * factor + offset is treated as a suspected
# misread until it repeats. Decreases always require confirmation (they're
# either spending — real but rare per-poll — or a misread).
SUSPECT_FACTOR = 3.0
SUSPECT_OFFSET = 50_000.0

SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"


@dataclass
class StageRecord:
    stage: str
    start_time: float
    end_time: float | None = None
    gold_earned: float = 0.0

    @property
    def duration(self) -> float:
        end = self.end_time if self.end_time is not None else time.time()
        return max(0.0, end - self.start_time)

    @property
    def gold_per_min(self) -> float:
        d = self.duration
        return (self.gold_earned / d) * 60 if d > 1 else 0.0

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_sec": round(self.duration, 1),
            "gold_earned": self.gold_earned,
            "gold_per_min": round(self.gold_per_min, 1),
        }


@dataclass
class _PendingValue:
    """A reading awaiting confirmation before being accepted."""

    value: object
    count: int = 1


@dataclass
class Tracker:
    session_log: Path | None = None

    current_stage: str | None = None
    current_record: StageRecord | None = None
    last_gold: float | None = None
    history: list[StageRecord] = field(default_factory=list)
    session_start: float = field(default_factory=time.time)
    total_earned: float = 0.0

    _pending_stage: _PendingValue | None = None
    _pending_gold: _PendingValue | None = None

    def start_session_log(self) -> None:
        SESSIONS_DIR.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.session_start))
        self.session_log = SESSIONS_DIR / f"session-{stamp}.jsonl"

    def update(self, gold: float | None, stage: str | None, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if stage is not None:
            self._update_stage(stage, now)
        if gold is not None:
            self._update_gold(gold)

    # -- start/stop ------------------------------------------------------

    def start(self, stage: str | None = None, now: float | None = None) -> None:
        """Begin a tracking segment.

        The gold baseline resets to the next reading, so gold gained while
        not tracking is never counted as earned.
        """
        now = time.time() if now is None else now
        self._pending_stage = None
        self._pending_gold = None
        self.last_gold = None
        if stage is not None:
            self.current_stage = stage
        if self.current_stage is not None and self.current_record is None:
            self.current_record = StageRecord(stage=self.current_stage, start_time=now)

    def stop(self, now: float | None = None) -> None:
        """End the segment: the current stage record is finalized into the
        history (and the session log), where the ranking picks it up."""
        now = time.time() if now is None else now
        if self.current_record is not None:
            self.current_record.end_time = now
            self.history.append(self.current_record)
            self._log_record(self.current_record)
            self.current_record = None
        self._pending_stage = None
        self._pending_gold = None

    # -- stage ---------------------------------------------------------

    def set_stage(self, stage: str, now: float | None = None) -> None:
        """Manual stage override: accepted immediately, no confirmation."""
        now = time.time() if now is None else now
        self._pending_stage = None
        if stage != self.current_stage:
            self._change_stage(stage, now)

    def _update_stage(self, stage: str, now: float) -> None:
        if stage == self.current_stage:
            self._pending_stage = None
            return
        if self._pending_stage and self._pending_stage.value == stage:
            self._pending_stage.count += 1
        else:
            self._pending_stage = _PendingValue(stage)
        if self.current_stage is None or self._pending_stage.count >= CONFIRM_READS:
            self._change_stage(stage, now)
            self._pending_stage = None

    def _change_stage(self, stage: str, now: float) -> None:
        if self.current_record is not None:
            self.current_record.end_time = now
            self.history.append(self.current_record)
            self._log_record(self.current_record)
        self.current_stage = stage
        self.current_record = StageRecord(stage=stage, start_time=now)

    # -- gold ----------------------------------------------------------

    def _update_gold(self, gold: float) -> None:
        if self.last_gold is None:
            self.last_gold = gold
            return

        delta = gold - self.last_gold
        plausible = 0 <= delta <= self.last_gold * SUSPECT_FACTOR + SUSPECT_OFFSET
        if not plausible:
            if self._pending_gold and self._pending_gold.value == gold:
                self._pending_gold.count += 1
            else:
                self._pending_gold = _PendingValue(gold)
            if self._pending_gold.count < CONFIRM_READS:
                return  # hold off — likely a misread
            self._pending_gold = None
        else:
            self._pending_gold = None

        if delta > 0:
            if self.current_record is not None:
                self.current_record.gold_earned += delta
            self.total_earned += delta
        self.last_gold = gold

    # -- persistence ----------------------------------------------------

    def _log_record(self, record: StageRecord) -> None:
        if self.session_log is None:
            return
        try:
            with self.session_log.open("a") as fh:
                fh.write(json.dumps(record.to_dict()) + "\n")
        except OSError:
            pass  # stats logging must never crash the meter


def fmt_gold(value: float | None) -> str:
    """Human format: 1234 → '1,234'; 1234567 → '1.23M'."""
    if value is None:
        return "—"
    for suffix, factor in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(value) >= factor:
            return f"{value / factor:.2f}{suffix}"
    return f"{value:,.0f}"


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
