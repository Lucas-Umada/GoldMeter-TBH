"""Configuration load/save for TBH Gold Meter.

Config lives in config.json next to the project root. Regions are stored
relative to the game window's top-left corner so the window can move
between sessions without recalibrating.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


@dataclass
class Region:
    """A rectangle relative to the game window: x, y, width, height."""

    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0

    def is_valid(self) -> bool:
        return self.w > 2 and self.h > 2

    def as_box(self) -> tuple[int, int, int, int]:
        """PIL crop box (left, top, right, bottom)."""
        return (self.x, self.y, self.x + self.w, self.y + self.h)


@dataclass
class Config:
    # Substring matched (case-insensitive) against window titles.
    # The game's real title has no spaces: "TaskBarHero".
    window_title: str = "TaskBarHero"
    poll_interval: float = 1.0
    # OCR upscale factor applied before tesseract; pixel fonts need 3-6x.
    ocr_scale: int = 4
    # Save the cropped/preprocessed images to debug/ on every poll.
    debug: bool = False
    gold_region: Region = field(default_factory=Region)
    stage_region: Region = field(default_factory=Region)

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        cfg = cls()
        for key in ("window_title", "poll_interval", "ocr_scale", "debug"):
            if key in data:
                setattr(cfg, key, data[key])
        for key in ("gold_region", "stage_region"):
            if key in data and isinstance(data[key], dict):
                setattr(cfg, key, Region(**data[key]))
        return cfg

    def is_calibrated(self) -> bool:
        # Only gold is required; the stage region can be skipped at
        # calibration and the stage typed manually in the meter instead.
        return self.gold_region.is_valid()
