"""Interactive calibration: drag rectangles over a live screenshot of the
game window to mark where the GOLD counter and the STAGE label are.

Selections are OCR'd immediately so you can verify the reading before
saving. Regions are stored window-relative in config.json.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageTk

from .capture import GameCapture
from .config import Config, Region
from . import ocr

_STEPS = [
    ("gold_region", "Drag a box around the GOLD amount, then press Enter"),
    ("stage_region", "Drag a box around the STAGE number, then press Enter "
                     "— or press S to skip and type the stage in the meter"),
]


class Calibrator:
    def __init__(self, cfg: Config, screenshot: Image.Image):
        self.cfg = cfg
        self.shot = screenshot
        # Scale the (tiny) game window up so regions are easy to drag.
        self.view_scale = max(1, min(6, 900 // max(1, screenshot.width)))

        self.root = tk.Tk()
        self.root.title("TBH Gold Meter — Calibration")
        self.step = 0
        self.drag_start: tuple[int, int] | None = None
        self.rect_id: int | None = None
        self.selection: Region | None = None

        view = screenshot.resize(
            (screenshot.width * self.view_scale, screenshot.height * self.view_scale),
            Image.NEAREST,
        )
        self.photo = ImageTk.PhotoImage(view)

        self.info = tk.Label(
            self.root, text="", font=tkfont.Font(size=12, weight="bold"),
            pady=6, fg="#222",
        )
        self.info.pack(fill="x")
        self.preview = tk.Label(self.root, text="", fg="#0a6", pady=2)
        self.preview.pack(fill="x")

        self.canvas = tk.Canvas(
            self.root, width=view.width, height=view.height,
            cursor="crosshair", highlightthickness=0,
        )
        self.canvas.pack()
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.root.bind("<Return>", self._on_confirm)
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.bind("s", self._on_skip)
        self.root.bind("S", self._on_skip)

        self._show_step()

    def _show_step(self) -> None:
        self.info.config(text=f"Step {self.step + 1}/{len(_STEPS)}: {_STEPS[self.step][1]}")
        self.preview.config(text="(Esc cancels without saving)")
        self.selection = None

    def _on_press(self, event) -> None:
        self.drag_start = (event.x, event.y)
        if self.rect_id is not None:
            self.canvas.delete(self.rect_id)
            self.rect_id = None

    def _on_drag(self, event) -> None:
        if self.drag_start is None:
            return
        x0, y0 = self.drag_start
        if self.rect_id is None:
            self.rect_id = self.canvas.create_rectangle(
                x0, y0, event.x, event.y, outline="#ff3030", width=2
            )
        else:
            self.canvas.coords(self.rect_id, x0, y0, event.x, event.y)
        s = self.view_scale
        left, top = min(x0, event.x) // s, min(y0, event.y) // s
        w, h = abs(event.x - x0) // s, abs(event.y - y0) // s
        self.selection = Region(left, top, w, h)
        self._update_preview()

    def _update_preview(self) -> None:
        if self.selection is None or not self.selection.is_valid():
            return
        crop = self.shot.crop(self.selection.as_box())
        attr = _STEPS[self.step][0]
        try:
            if attr == "gold_region":
                value, raw = ocr.read_gold(crop, self.cfg.ocr_scale)
                shown = ocr.parse_gold(raw)
                text = f"OCR reads: {raw!r} → gold = {shown}"
            else:
                stage, raw = ocr.read_stage(crop, self.cfg.ocr_scale)
                text = f"OCR reads: {raw!r} → stage = {stage}"
        except Exception as exc:  # tesseract missing, etc.
            text = f"OCR preview unavailable: {exc}"
        self.preview.config(text=text)

    def _on_confirm(self, _event) -> None:
        if self.selection is None or not self.selection.is_valid():
            self.preview.config(text="Draw a selection first (click and drag).")
            return
        setattr(self.cfg, _STEPS[self.step][0], self.selection)
        if self.rect_id is not None:
            self.canvas.itemconfig(self.rect_id, outline="#30c030")
            self.rect_id = None
        self._advance()

    def _on_skip(self, _event=None) -> None:
        # Only the stage region is optional — gold must be calibrated.
        if _STEPS[self.step][0] != "stage_region":
            return
        self.cfg.stage_region = Region()
        if self.rect_id is not None:
            self.canvas.delete(self.rect_id)
            self.rect_id = None
        print("Stage region skipped — type the stage in the meter window.")
        self._advance()

    def _advance(self) -> None:
        self.step += 1
        if self.step >= len(_STEPS):
            self.cfg.save()
            self.root.destroy()
            print(f"Calibration saved to config.json")
            return
        self._show_step()

    def run(self) -> None:
        self.root.mainloop()


def main(cfg: Config) -> int:
    cap = GameCapture(cfg.window_title)
    try:
        info = cap.find()
    except Exception as exc:
        print(f"Cannot find the game window: {exc}")
        print("Is the game running? Use 'python -m goldmeter windows' to list "
              "window titles and set window_title in config.json.")
        return 1
    print(f"Capturing window: {info.title!r} ({info.width}x{info.height})")
    Calibrator(cfg, cap.grab()).run()
    return 0
