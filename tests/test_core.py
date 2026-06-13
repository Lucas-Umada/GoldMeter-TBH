"""Unit tests for parsing, tracking, ranking and preprocessing.

Run with:  .venv\\Scripts\\python -m unittest discover tests
The OCR end-to-end test runs only when tesseract is installed.
"""

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from goldmeter import ranking
from goldmeter.ocr import parse_gold, parse_stage, preprocess, tesseract_available
from goldmeter.tracker import Tracker, fmt_gold, fmt_duration


class TestParseGold(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(parse_gold("987"), 987)

    def test_thousands_separators(self):
        self.assertEqual(parse_gold("1,234,567"), 1_234_567)
        self.assertEqual(parse_gold("1.234.567"), 1_234_567)

    def test_suffixes(self):
        self.assertEqual(parse_gold("1.23M"), 1_230_000)
        self.assertEqual(parse_gold("12,5K"), 12_500)
        self.assertEqual(parse_gold("2B"), 2_000_000_000)
        self.assertEqual(parse_gold("1.5t"), 1_500_000_000_000)

    def test_garbage(self):
        self.assertIsNone(parse_gold(""))
        self.assertIsNone(parse_gold("..,,"))


class TestParseStage(unittest.TestCase):
    def test_act_stage(self):
        self.assertEqual(parse_stage("2-6"), "2-6")
        self.assertEqual(parse_stage(" 12 - 3 "), "12-3")

    def test_bare_number(self):
        self.assertEqual(parse_stage("42"), "42")

    def test_garbage(self):
        self.assertIsNone(parse_stage(""))
        self.assertIsNone(parse_stage("--"))


class TestTracker(unittest.TestCase):
    def test_gold_accumulates_per_stage(self):
        t = Tracker()
        t.update(100, "1-1", now=0)
        t.update(150, "1-1", now=1)
        t.update(220, "1-1", now=2)
        self.assertEqual(t.current_record.gold_earned, 120)

    def test_stage_change_needs_confirmation(self):
        t = Tracker()
        t.update(100, "1-1", now=0)
        t.update(100, "1-2", now=1)  # first sighting: not yet accepted
        self.assertEqual(t.current_stage, "1-1")
        t.update(100, "1-2", now=2)  # confirmed
        self.assertEqual(t.current_stage, "1-2")
        self.assertEqual(len(t.history), 1)
        self.assertEqual(t.history[0].stage, "1-1")

    def test_stage_flicker_rejected(self):
        t = Tracker()
        t.update(100, "1-1", now=0)
        t.update(100, "7-7", now=1)  # OCR glitch
        t.update(100, "1-1", now=2)
        self.assertEqual(t.current_stage, "1-1")
        self.assertEqual(len(t.history), 0)

    def test_misread_spike_rejected_once(self):
        t = Tracker()
        t.update(1000, "1-1", now=0)
        t.update(900_000_000, "1-1", now=1)  # absurd jump: held
        self.assertEqual(t.last_gold, 1000)
        t.update(1100, "1-1", now=2)  # normal reading resumes
        self.assertEqual(t.last_gold, 1100)
        self.assertEqual(t.current_record.gold_earned, 100)

    def test_persistent_jump_accepted(self):
        t = Tracker()
        t.update(1000, "1-1", now=0)
        t.update(900_000_000, "1-1", now=1)
        t.update(900_000_000, "1-1", now=2)  # repeated → real
        self.assertEqual(t.last_gold, 900_000_000)

    def test_manual_stage_applies_immediately(self):
        t = Tracker()
        t.update(100, "1-1", now=0)
        t.set_stage("3-4", now=5)  # manual: no confirmation needed
        self.assertEqual(t.current_stage, "3-4")
        self.assertEqual(len(t.history), 1)
        self.assertEqual(t.history[0].stage, "1-1")

    def test_manual_stage_collects_gold_without_ocr_stage(self):
        t = Tracker()
        t.set_stage("2-6", now=0)
        t.update(100, None, now=1)   # stage OCR disabled → stage is None
        t.update(160, None, now=2)
        self.assertEqual(t.current_stage, "2-6")
        self.assertEqual(t.current_record.gold_earned, 60)

    def test_stop_finalizes_segment_into_history(self):
        t = Tracker()
        t.start("2-6", now=0)
        t.update(100, None, now=1)
        t.update(700, None, now=61)
        t.stop(now=61)
        self.assertIsNone(t.current_record)
        self.assertEqual(len(t.history), 1)
        self.assertEqual(t.history[0].stage, "2-6")
        self.assertEqual(t.history[0].gold_earned, 600)
        self.assertEqual(t.history[0].duration, 61)

    def test_gold_gained_while_stopped_not_counted(self):
        t = Tracker()
        t.start("2-6", now=0)
        t.update(100, None, now=1)
        t.update(200, None, now=2)
        t.stop(now=2)
        # farms to 50_000 while paused, then resumes
        t.start("2-6", now=100)
        t.update(50_000, None, now=101)  # new baseline, not a gain
        t.update(50_300, None, now=102)
        self.assertEqual(t.current_record.gold_earned, 300)
        self.assertEqual(t.total_earned, 400)

    def test_restart_same_stage_starts_fresh_record(self):
        t = Tracker()
        t.start("2-6", now=0)
        t.stop(now=10)
        t.start(now=20)  # no stage given: resumes the last known stage
        self.assertEqual(t.current_stage, "2-6")
        self.assertIsNotNone(t.current_record)
        self.assertEqual(t.current_record.start_time, 20)

    def test_spending_does_not_reduce_earned(self):
        t = Tracker()
        t.update(1000, "1-1", now=0)
        t.update(1500, "1-1", now=1)
        t.update(200, "1-1", now=2)   # spent gold: held once
        t.update(200, "1-1", now=3)   # confirmed drop
        t.update(300, "1-1", now=4)
        self.assertEqual(t.current_record.gold_earned, 600)
        self.assertEqual(t.last_gold, 300)


class TestFormatting(unittest.TestCase):
    def test_fmt_gold(self):
        self.assertEqual(fmt_gold(None), "—")
        self.assertEqual(fmt_gold(950), "950")
        self.assertEqual(fmt_gold(1_234_567), "1.23M")
        self.assertEqual(fmt_gold(2_500_000_000), "2.50B")

    def test_fmt_duration(self):
        self.assertEqual(fmt_duration(75), "1:15")
        self.assertEqual(fmt_duration(3725), "1:02:05")


def _rec(stage, gold, seconds):
    return {"stage": stage, "gold_earned": gold, "duration_sec": seconds}


class TestRanking(unittest.TestCase):
    def test_aggregates_repeat_visits(self):
        stats = ranking.aggregate([_rec("1-1", 600, 60), _rec("1-1", 400, 40)])
        s = stats["1-1"]
        self.assertEqual(s.gold_earned, 1000)
        self.assertEqual(s.duration, 100)
        self.assertEqual(s.visits, 2)
        self.assertEqual(s.gold_per_min, 600)

    def test_rank_orders_by_gold_per_min(self):
        stats = ranking.aggregate([
            _rec("1-1", 600, 60),    # 600/min
            _rec("2-3", 3000, 120),  # 1500/min
            _rec("1-2", 100, 60),    # 100/min
        ])
        self.assertEqual([s.stage for s in ranking.rank(stats)], ["2-3", "1-1", "1-2"])

    def test_short_visits_sink_to_bottom(self):
        stats = ranking.aggregate([
            _rec("9-9", 5000, 3),   # absurd rate, but only 3s — not rankable
            _rec("1-1", 600, 60),
        ])
        self.assertEqual([s.stage for s in ranking.rank(stats)], ["1-1", "9-9"])

    def test_garbage_records_skipped(self):
        stats = ranking.aggregate([
            {"stage": None, "gold_earned": 1, "duration_sec": 1},
            {"gold_earned": 1},
            {"stage": "1-1", "gold_earned": "x", "duration_sec": 1},
            _rec("1-1", 100, 10),
        ])
        self.assertEqual(list(stats), ["1-1"])
        self.assertEqual(stats["1-1"].visits, 1)

    def test_load_skips_excluded_and_bad_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "session-a.jsonl").write_text(
                json.dumps(_rec("1-1", 100, 10)) + "\nnot json\n", encoding="utf-8"
            )
            (d / "session-b.jsonl").write_text(
                json.dumps(_rec("2-2", 200, 20)) + "\n", encoding="utf-8"
            )
            records = ranking.load_session_records(d, exclude=d / "session-b.jsonl")
            self.assertEqual(records, [_rec("1-1", 100, 10)])


def _render(text: str, fg, bg) -> Image.Image:
    """Light-on-dark HUD-like text image at a realistic glyph size."""
    try:
        font = ImageFont.load_default(24)  # Pillow ≥10.1: scalable default
    except TypeError:
        font = ImageFont.load_default()
    img = Image.new("RGB", (16 + 16 * len(text), 44), bg)
    ImageDraw.Draw(img).text((8, 8), text, fill=fg, font=font)
    return img


class TestPreprocess(unittest.TestCase):
    def test_output_is_binary_dark_on_light(self):
        pre = preprocess(_render("12,345", (255, 215, 80), (20, 20, 30)), scale=4)
        hist = pre.histogram()
        self.assertEqual(sum(hist[1:255]), 0)  # fully binarized
        # majority background must be white (dark glyphs on light)
        self.assertGreater(hist[255], hist[0])


@unittest.skipUnless(tesseract_available(), "tesseract not installed")
class TestOcrEndToEnd(unittest.TestCase):
    def test_reads_synthetic_gold(self):
        from goldmeter.ocr import read_gold

        value, raw = read_gold(_render("12,345", (255, 215, 80), (20, 20, 30)), scale=6)
        self.assertEqual(value, 12_345, f"raw OCR text was {raw!r}")

    def test_reads_synthetic_stage(self):
        from goldmeter.ocr import read_stage

        stage, raw = read_stage(_render("2-6", (240, 240, 240), (10, 30, 10)), scale=6)
        self.assertEqual(stage, "2-6", f"raw OCR text was {raw!r}")


if __name__ == "__main__":
    unittest.main()
