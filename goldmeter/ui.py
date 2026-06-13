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
from . import ocr, ranking

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
            gold, gold_raw = ocr.read_gold(gold_crop, cfg.ocr_scale)
            # Stage region is optional: it can be skipped at calibration and
            # the stage typed manually in the meter instead.
            stage, stage_raw, stage_crop = None, "", None
            if cfg.stage_region.is_valid():
                stage_crop = frame.crop(cfg.stage_region.as_box())
                stage, stage_raw = ocr.read_stage(stage_crop, cfg.ocr_scale)
            if cfg.debug and debug_saved < 200:
                DEBUG_DIR.mkdir(exist_ok=True)
                gold_crop.save(DEBUG_DIR / f"gold-{debug_saved:04d}.png")
                if stage_crop is not None:
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
        # Finished stages from past sessions; the live session is layered on
        # top of these at refresh time (its own log file is excluded so the
        # stages it writes aren't counted twice).
        self.base_records = ranking.load_session_records(
            exclude=self.tracker.session_log
        )
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

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Meter.Treeview", background="#22252d", fieldbackground="#22252d",
            foreground=FG, borderwidth=0, rowheight=22,
        )
        style.configure("Meter.Treeview.Heading", background="#2a2e38", foreground=DIM)
        style.configure("Meter.TNotebook", background=BG, borderwidth=0)
        style.configure(
            "Meter.TNotebook.Tab", background="#2a2e38", foreground=DIM, padding=(10, 4)
        )
        style.map(
            "Meter.TNotebook.Tab",
            background=[("selected", "#22252d")], foreground=[("selected", FG)],
        )

        notebook = ttk.Notebook(self.root, style="Meter.TNotebook")
        notebook.pack(fill="both", expand=True, padx=14, pady=(8, 0))

        rank_frame = tk.Frame(notebook, bg=BG)
        cols = ("rank", "stage", "visits", "time", "gold", "rate")
        self.rank_tree = ttk.Treeview(
            rank_frame, columns=cols, show="headings", style="Meter.Treeview", height=8
        )
        for col, title, width, anchor in (
            ("rank", "#", 30, "e"),
            ("stage", "Stage", 60, "w"),
            ("visits", "Visits", 50, "e"),
            ("time", "Time", 75, "e"),
            ("gold", "Gold", 90, "e"),
            ("rate", "Gold/min", 85, "e"),
        ):
            self.rank_tree.heading(col, text=title)
            self.rank_tree.column(col, width=width, anchor=anchor)
        self.rank_tree.pack(fill="both", expand=True)
        notebook.add(rank_frame, text="Stage ranking")

        hist_frame = tk.Frame(notebook, bg=BG)
        cols = ("stage", "time", "gold", "rate")
        self.tree = ttk.Treeview(
            hist_frame, columns=cols, show="headings", style="Meter.Treeview", height=8
        )
        for col, title, width, anchor in (
            ("stage", "Stage", 70, "w"),
            ("time", "Time", 80, "e"),
            ("gold", "Gold earned", 110, "e"),
            ("rate", "Gold/min", 90, "e"),
        ):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor=anchor)
        self.tree.pack(fill="both", expand=True)
        notebook.add(hist_frame, text="This session")

        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="x", padx=14, pady=(6, 10))

        # Tracking is off until the user presses start; while off, readings
        # are shown live but nothing is accumulated or logged.
        self.tracking = False
        self.live_gold: float | None = None
        self.live_stage: str | None = None
        self.track_btn = tk.Button(
            bottom, text="▶ start", command=self._toggle_tracking,
            bg="#2d4a36", fg=FG, activebackground="#386044", activeforeground=FG,
            font=small, relief="flat", padx=10, highlightthickness=0,
        )
        self.track_btn.pack(side="left", padx=(0, 10))

        self.status = label(bottom, text="starting…", font=small, fg=DIM, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.topmost = tk.BooleanVar(value=False)
        tk.Checkbutton(
            bottom, text="on top", variable=self.topmost, command=self._toggle_top,
            bg=BG, fg=DIM, selectcolor=BG, activebackground=BG, activeforeground=FG,
            font=small, highlightthickness=0,
        ).pack(side="right")

        # Manual stage override: type the stage (e.g. 2-6) and press Enter
        # when OCR can't read the stage label. Empty + Enter returns to OCR.
        self.manual_stage: str | None = None
        tk.Button(
            bottom, text="set", command=self._set_manual_stage,
            bg="#2a2e38", fg=FG, activebackground="#343945", activeforeground=FG,
            font=small, relief="flat", padx=8, highlightthickness=0,
        ).pack(side="right", padx=(2, 10))
        self.stage_entry = tk.Entry(
            bottom, width=7, bg="#22252d", fg=FG, insertbackground=FG,
            relief="flat", font=small, justify="center",
        )
        self.stage_entry.pack(side="right", padx=(4, 0), ipady=2)
        self.stage_entry.bind("<Return>", self._set_manual_stage)
        label(bottom, text="stage:", font=small, fg=DIM).pack(side="right")

        self.worker = threading.Thread(
            target=_worker, args=(self.cfg, self.readings, self.stop), daemon=True
        )
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _toggle_top(self) -> None:
        self.root.attributes("-topmost", self.topmost.get())

    def _toggle_tracking(self) -> None:
        if self.tracking:
            self.tracking = False
            self.tracker.stop()
            self.track_btn.config(text="▶ start", bg="#2d4a36", activebackground="#386044")
            self.status.config(text="stopped — stage saved to history", fg=DIM)
        else:
            self.tracking = True
            # Start on the freshest stage we know, not a stale tracker one.
            self.tracker.start(self.manual_stage or self.live_stage)
            self.track_btn.config(text="■ stop", bg="#5a2d2d", activebackground="#703838")
            self.status.config(text="tracking…", fg=OK)
        self._refresh()

    def _set_manual_stage(self, _event=None) -> None:
        value = self.stage_entry.get().strip()
        if value:
            self.manual_stage = value
            if self.tracking:
                self.tracker.set_stage(value)
            self.status.config(
                text=f"stage set to {value} (manual — clear the box to go back to OCR)",
                fg=OK,
            )
        else:
            self.manual_stage = None
            self.status.config(text="manual stage cleared — back to OCR", fg=OK)
        self._refresh()

    def _close(self) -> None:
        if self.tracking:
            self.tracker.stop()  # don't lose the segment in progress
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
                if r.gold is not None:
                    self.live_gold = r.gold
                if r.stage is not None:
                    self.live_stage = r.stage
                if self.tracking:
                    # With a manual stage set, OCR stage readings are ignored.
                    stage = None if self.manual_stage else r.stage
                    self.tracker.update(r.gold, stage)
                parts = []
                if r.gold is None:
                    parts.append(f"gold unreadable ({r.gold_raw!r})")
                if (r.stage is None and self.manual_stage is None
                        and self.cfg.stage_region.is_valid()):
                    parts.append(f"stage unreadable ({r.stage_raw!r})")
                if parts:
                    self.status.config(text="OCR: " + ", ".join(parts), fg=ERR)
                elif self.tracking:
                    self.status.config(text="tracking…", fg=OK)
                else:
                    self.status.config(text="paused — press ▶ start to track", fg=DIM)
            updated = True
        if updated:
            self._refresh()
        self.root.after(200, self._drain)

    def _refresh(self) -> None:
        t = self.tracker
        if self.tracking:
            shown_stage = t.current_stage
        else:
            shown_stage = self.manual_stage or self.live_stage
        self.stage_lbl.config(text=shown_stage or "—")
        gold = self.live_gold if self.live_gold is not None else t.last_gold
        self.gold_lbl.config(text=fmt_gold(gold))

        rec = t.current_record
        self.stat_lbls["stage_gold"].config(
            text=fmt_gold(rec.gold_earned) if rec else "—", fg=GOLD
        )
        self.stat_lbls["stage_time"].config(text=fmt_duration(rec.duration) if rec else "—")
        self.stat_lbls["stage_rate"].config(
            text=fmt_gold(rec.gold_per_min) + "/min" if rec else "—"
        )
        self.stat_lbls["session_gold"].config(text=fmt_gold(t.total_earned), fg=GOLD)
        # Rate over time actually tracked, so paused time doesn't dilute it.
        tracked_min = (sum(r.duration for r in t.history)
                       + (rec.duration if rec else 0.0)) / 60
        rate = t.total_earned / tracked_min if tracked_min > 0.2 else 0.0
        self.stat_lbls["session_rate"].config(text=fmt_gold(rate) + "/min")

        shown = len(self.tree.get_children())
        for rec in t.history[shown:]:
            self.tree.insert(
                "", 0,
                values=(rec.stage, fmt_duration(rec.duration),
                        fmt_gold(rec.gold_earned), fmt_gold(rec.gold_per_min)),
            )
        self._refresh_ranking()

    def _refresh_ranking(self) -> None:
        """Rebuild the all-time ranking: past sessions + this session live."""
        t = self.tracker
        records = list(self.base_records)
        records.extend(rec.to_dict() for rec in t.history)
        if t.current_record is not None:
            records.append(t.current_record.to_dict())
        ranked = ranking.rank(ranking.aggregate(records))

        self.rank_tree.delete(*self.rank_tree.get_children())
        for pos, s in enumerate(ranked, start=1):
            stage = f"▶ {s.stage}" if s.stage == t.current_stage else s.stage
            self.rank_tree.insert(
                "", "end",
                values=(pos, stage, s.visits, fmt_duration(s.duration),
                        fmt_gold(s.gold_earned), fmt_gold(s.gold_per_min)),
            )

    def run(self) -> None:
        self._refresh_ranking()  # show past-session ranking immediately
        self.worker.start()
        self.root.after(200, self._drain)
        self.root.mainloop()


def main(cfg: Config) -> int:
    if not cfg.is_calibrated():
        print("Not calibrated yet — run:  python -m goldmeter calibrate")
        return 1
    MeterUI(cfg).run()
    return 0
