"""Live stats window. A worker thread captures + OCRs the game window; the
Tk main loop consumes readings from a queue and updates the display."""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from pathlib import Path

from .capture import GameCapture, WindowNotFound
from .config import Config
from .tracker import Tracker, fmt_gold, fmt_duration
from . import ocr

BG = "#1b1d23"
FG = "#e8e6e3"
GOLD = "#f2c14e"
DIM = "#8a8f98"
OK = "#5fb878"
ERR = "#e06c75"

DEBUG_DIR = Path(__file__).resolve().parent.parent / "debug"


class Reading:
    __slots__ = ("gold", "gold_raw", "stage", "stage_raw", "error")

    def __init__(self, gold=None, gold_raw="", stage=None, stage_raw="", error=None):
        self.gold = gold
        self.gold_raw = gold_raw
        self.stage = stage
        self.stage_raw = stage_raw
        self.error = error


def _worker(cfg: Config, out: queue.Queue, stop: threading.Event) -> None:
    cap = GameCapture(cfg.window_title)
    debug_saved = 0
    while not stop.is_set():
        started = time.time()
        try:
            frame = cap.grab()
            gold_crop = frame.crop(cfg.gold_region.as_box())
            stage_crop = frame.crop(cfg.stage_region.as_box())
            gold, gold_raw = ocr.read_gold(gold_crop, cfg.ocr_scale)
            stage, stage_raw = ocr.read_stage(stage_crop, cfg.ocr_scale)
            if cfg.debug and debug_saved < 200:
                DEBUG_DIR.mkdir(exist_ok=True)
                gold_crop.save(DEBUG_DIR / f"gold-{debug_saved:04d}.png")
                stage_crop.save(DEBUG_DIR / f"stage-{debug_saved:04d}.png")
                debug_saved += 1
            out.put(Reading(gold, gold_raw, stage, stage_raw))
        except WindowNotFound as exc:
            out.put(Reading(error=f"Game window not found — {exc}"))
        except Exception as exc:
            out.put(Reading(error=f"{type(exc).__name__}: {exc}"))
        # Keep cadence steady regardless of OCR time.
        elapsed = time.time() - started
        stop.wait(max(0.1, cfg.poll_interval - elapsed))


class MeterUI:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tracker = Tracker()
        self.tracker.start_session_log()
        self.readings: queue.Queue = queue.Queue()
        self.stop = threading.Event()

        self.root = tk.Tk()
        self.root.title("TBH Gold Meter")
        self.root.configure(bg=BG)
        self.root.minsize(360, 420)

        big = tkfont.Font(size=22, weight="bold")
        med = tkfont.Font(size=12)
        small = tkfont.Font(size=9)

        def label(parent, **kw):
            kw.setdefault("bg", BG)
            kw.setdefault("fg", FG)
            return tk.Label(parent, **kw)

        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 4))
        label(head, text="STAGE", font=small, fg=DIM).grid(row=0, column=0, sticky="w")
        label(head, text="CURRENT GOLD", font=small, fg=DIM).grid(row=0, column=1, sticky="e")
        head.columnconfigure(0, weight=1)
        head.columnconfigure(1, weight=1)
        self.stage_lbl = label(head, text="—", font=big)
        self.stage_lbl.grid(row=1, column=0, sticky="w")
        self.gold_lbl = label(head, text="—", font=big, fg=GOLD)
        self.gold_lbl.grid(row=1, column=1, sticky="e")

        stats = tk.Frame(self.root, bg=BG)
        stats.pack(fill="x", padx=14, pady=8)
        rows = [
            ("Gold this stage", "stage_gold"),
            ("Time on stage", "stage_time"),
            ("Gold/min (stage)", "stage_rate"),
            ("Session total", "session_gold"),
            ("Session gold/min", "session_rate"),
        ]
        self.stat_lbls: dict[str, tk.Label] = {}
        for i, (title, key) in enumerate(rows):
            label(stats, text=title, font=med, fg=DIM).grid(row=i, column=0, sticky="w", pady=1)
            value = label(stats, text="—", font=med)
            value.grid(row=i, column=1, sticky="e", pady=1)
            self.stat_lbls[key] = value
        stats.columnconfigure(1, weight=1)

        label(self.root, text="STAGE HISTORY", font=small, fg=DIM, anchor="w").pack(
            fill="x", padx=14, pady=(8, 2)
        )
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Meter.Treeview", background="#22252d", fieldbackground="#22252d",
            foreground=FG, borderwidth=0, rowheight=22,
        )
        style.configure("Meter.Treeview.Heading", background="#2a2e38", foreground=DIM)
        cols = ("stage", "time", "gold", "rate")
        self.tree = ttk.Treeview(
            self.root, columns=cols, show="headings", style="Meter.Treeview", height=8
        )
        for col, title, width, anchor in (
            ("stage", "Stage", 70, "w"),
            ("time", "Time", 80, "e"),
            ("gold", "Gold earned", 110, "e"),
            ("rate", "Gold/min", 90, "e"),
        ):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor=anchor)
        self.tree.pack(fill="both", expand=True, padx=14)

        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="x", padx=14, pady=(6, 10))
        self.status = label(bottom, text="starting…", font=small, fg=DIM, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.topmost = tk.BooleanVar(value=False)
        tk.Checkbutton(
            bottom, text="on top", variable=self.topmost, command=self._toggle_top,
            bg=BG, fg=DIM, selectcolor=BG, activebackground=BG, activeforeground=FG,
            font=small, highlightthickness=0,
        ).pack(side="right")

        self.worker = threading.Thread(
            target=_worker, args=(self.cfg, self.readings, self.stop), daemon=True
        )
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _toggle_top(self) -> None:
        self.root.attributes("-topmost", self.topmost.get())

    def _close(self) -> None:
        self.stop.set()
        self.root.destroy()

    def _drain(self) -> None:
        updated = False
        while True:
            try:
                r: Reading = self.readings.get_nowait()
            except queue.Empty:
                break
            if r.error:
                self.status.config(text=r.error, fg=ERR)
            else:
                self.tracker.update(r.gold, r.stage)
                parts = []
                if r.gold is None:
                    parts.append(f"gold unreadable ({r.gold_raw!r})")
                if r.stage is None:
                    parts.append(f"stage unreadable ({r.stage_raw!r})")
                if parts:
                    self.status.config(text="OCR: " + ", ".join(parts), fg=ERR)
                else:
                    self.status.config(text="reading OK", fg=OK)
            updated = True
        if updated:
            self._refresh()
        self.root.after(200, self._drain)

    def _refresh(self) -> None:
        t = self.tracker
        self.stage_lbl.config(text=t.current_stage or "—")
        self.gold_lbl.config(text=fmt_gold(t.last_gold))

        rec = t.current_record
        self.stat_lbls["stage_gold"].config(
            text=fmt_gold(rec.gold_earned) if rec else "—", fg=GOLD
        )
        self.stat_lbls["stage_time"].config(text=fmt_duration(rec.duration) if rec else "—")
        self.stat_lbls["stage_rate"].config(
            text=fmt_gold(rec.gold_per_min) + "/min" if rec else "—"
        )
        self.stat_lbls["session_gold"].config(text=fmt_gold(t.total_earned), fg=GOLD)
        session_min = (time.time() - t.session_start) / 60
        rate = t.total_earned / session_min if session_min > 0.2 else 0.0
        self.stat_lbls["session_rate"].config(text=fmt_gold(rate) + "/min")

        shown = len(self.tree.get_children())
        for rec in t.history[shown:]:
            self.tree.insert(
                "", 0,
                values=(rec.stage, fmt_duration(rec.duration),
                        fmt_gold(rec.gold_earned), fmt_gold(rec.gold_per_min)),
            )

    def run(self) -> None:
        self.worker.start()
        self.root.after(200, self._drain)
        self.root.mainloop()


def main(cfg: Config) -> int:
    if not cfg.is_calibrated():
        print("Not calibrated yet — run:  python -m goldmeter calibrate")
        return 1
    MeterUI(cfg).run()
    return 0
