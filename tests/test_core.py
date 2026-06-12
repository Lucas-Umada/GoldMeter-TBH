"""Unit tests for parsing, tracking and preprocessing.

Run with:  .venv/bin/python -m unittest discover tests
The OCR end-to-end test runs only when tesseract is installed.
"""

import shutil
import unittest

from PIL import Image, ImageDraw

from goldmeter.ocr import parse_gold, parse_stage, preprocess
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


def _render(text: str, fg, bg) -> Image.Image:
    """Small light-on-dark text image, like a game HUD."""
    img = Image.new("RGB", (120, 24), bg)
    ImageDraw.Draw(img).text((4, 4), text, fill=fg)
    return img


class TestPreprocess(unittest.TestCase):
    def test_output_is_binary_dark_on_light(self):
        pre = preprocess(_render("12,345", (255, 215, 80), (20, 20, 30)), scale=4)
        hist = pre.histogram()
        self.assertEqual(sum(hist[1:255]), 0)  # fully binarized
        # majority background must be white (dark glyphs on light)
        self.assertGreater(hist[255], hist[0])


@unittest.skipUnless(shutil.which("tesseract"), "tesseract not installed")
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
