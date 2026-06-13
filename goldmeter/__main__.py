"""Entry point: python -m goldmeter [run|calibrate|windows|test|rank]"""

from __future__ import annotations

import argparse
import sys

from .config import Config


def cmd_windows(cfg: Config) -> int:
    from .capture import GameCapture

    windows = GameCapture(cfg.window_title).list_windows()
    if not windows:
        print("No titled windows found.")
        return 1
    print(f"{'match':5}  {'size':11}  title")
    needle = cfg.window_title.lower()
    for w in sorted(windows, key=lambda w: w.title.lower()):
        mark = "  *  " if needle in w.title.lower() else "     "
        print(f"{mark}  {w.width}x{w.height:<6}  {w.title}")
    print(f"\n'*' = matches window_title {cfg.window_title!r} in config.json")
    return 0


def cmd_test(cfg: Config) -> int:
    """One-shot capture + OCR of both regions, with debug images saved."""
    from pathlib import Path
    from .capture import GameCapture
    from . import ocr

    if not cfg.is_calibrated():
        print("Not calibrated yet — run:  python -m goldmeter calibrate")
        return 1
    cap = GameCapture(cfg.window_title)
    info = cap.find()
    print(f"Window: {info.title!r} ({info.width}x{info.height})")
    frame = cap.grab()
    debug = Path(__file__).resolve().parent.parent / "debug"
    debug.mkdir(exist_ok=True)
    frame.save(debug / "test-window.png")
    for name, region, reader in (
        ("gold", cfg.gold_region, ocr.read_gold),
        ("stage", cfg.stage_region, ocr.read_stage),
    ):
        if not region.is_valid():
            print(f"{name:6} region not calibrated — skipped (stage can be "
                  "typed manually in the meter)")
            continue
        crop = frame.crop(region.as_box())
        crop.save(debug / f"test-{name}.png")
        ocr.preprocess(crop, cfg.ocr_scale).save(debug / f"test-{name}-pre.png")
        value, raw = reader(crop, cfg.ocr_scale)
        print(f"{name:6} region {region}: OCR={raw!r} → {value}")
    print(f"Crops saved in {debug}/")
    return 0


def cmd_rank(cfg: Config) -> int:
    """Print the all-time stage ranking from sessions/*.jsonl."""
    from . import ranking
    from .tracker import fmt_gold, fmt_duration

    ranked = ranking.rank(ranking.aggregate(ranking.load_session_records()))
    if not ranked:
        print("No finished stages recorded yet — run the meter first.")
        return 1
    print(f"{'#':>3}  {'stage':8} {'visits':>6}  {'time':>9}  {'gold':>10}  {'gold/min':>10}")
    for pos, s in enumerate(ranked, start=1):
        note = "" if s.duration >= ranking.MIN_RANKABLE_SECONDS else "  (too short to rank)"
        print(f"{pos:>3}  {s.stage:8} {s.visits:>6}  {fmt_duration(s.duration):>9}  "
              f"{fmt_gold(s.gold_earned):>10}  {fmt_gold(s.gold_per_min):>10}{note}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="goldmeter",
        description="TBH: Task Bar Hero gold/stage meter (screen OCR, display only).",
    )
    parser.add_argument(
        "command", nargs="?", default="run",
        choices=["run", "calibrate", "windows", "test", "rank"],
        help="run: live meter (default) · calibrate: select OCR regions · "
             "windows: list window titles · test: one-shot OCR check · "
             "rank: print all-time stage ranking",
    )
    parser.add_argument("--debug", action="store_true",
                        help="save OCR crops to debug/ while running")
    args = parser.parse_args()

    cfg = Config.load()
    if args.debug:
        cfg.debug = True

    if args.command == "windows":
        return cmd_windows(cfg)
    if args.command == "test":
        return cmd_test(cfg)
    if args.command == "rank":
        return cmd_rank(cfg)
    if args.command == "calibrate":
        from .calibrate import main as calibrate_main
        return calibrate_main(cfg)
    from .ui import main as ui_main
    return ui_main(cfg)


if __name__ == "__main__":
    sys.exit(main())
