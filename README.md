# TBH Gold Meter

A screen reader (OCR, display-only — no audio) for **TBH: Task Bar Hero**
on **Windows**. It captures the game window, reads the gold counter and the
stage label off the screen, and shows live stats in its own small window:

- current stage and current gold
- **gold earned on the current stage**, time on stage, gold/min
- session totals and a per-stage history table
- an **all-time stage ranking by gold/min** (which stage is the most
  profitable to farm), persisted across sessions
- every finished stage is appended to `sessions\session-*.jsonl`

It never touches the game's memory or files — it only looks at pixels.

## Requirements

- Windows 10/11 with Python 3.10+ (`python` available in PowerShell).
- Tesseract OCR (one-time):

  ```powershell
  winget install --id UB-Mannheim.TesseractOCR
  ```

  The default install location is auto-detected; no PATH changes needed.

- Python deps (creates `.venv\`):

  ```powershell
  .\setup.ps1
  ```

All commands below are for the VS Code integrated terminal (PowerShell).

## Usage

1. **Start the game.**
2. Check the meter can see it:

   ```powershell
   .\.venv\Scripts\python.exe -m goldmeter windows
   ```

   The game's title should appear with a `*`. If the title isn't matched,
   edit `window_title` in `config.json` to a substring of the real title.
3. **Calibrate once** (drag a box around the gold number, Enter, then around
   the stage number, Enter — the OCR preview shows what it reads live).
   If the stage label won't OCR reliably, press **S** on the stage step to
   skip it and type the stage manually in the meter instead:

   ```powershell
   .\.venv\Scripts\python.exe -m goldmeter calibrate
   ```

4. **Run the meter:**

   ```powershell
   .\.venv\Scripts\python.exe -m goldmeter
   ```

   The **Stage ranking** tab lists every stage you've ever played — visits,
   total time, total gold and gold/min — sorted best to worst, updating live
   and including all past sessions. The same table is available headless:

   ```powershell
   .\.venv\Scripts\python.exe -m goldmeter rank
   ```

### Start/stop tracking

The meter opens **paused**: gold and stage are shown live, but nothing is
recorded. Press **▶ start** to begin tracking the current stage and
**■ stop** to end the segment — it lands in the *This session* tab, is
appended to the session log, and from then on counts toward the all-time
*Stage ranking*. The gold baseline resets on every start, so gold gained
while paused is never counted, and the session gold/min only divides by
time actually tracked. Stage changes mid-tracking still close out segments
automatically.

### Manual stage input

When the stage label can't be read reliably (or you skipped its
calibration), type the stage into the **stage:** box at the bottom of the
meter and press Enter (or click *set*). It takes effect immediately, stage
OCR is ignored while it's set, and gold/min keeps being tracked under the
stage you typed — just update the box whenever you move to another stage.
Clear the box and press Enter to go back to OCR.

### Troubleshooting

- `python -m goldmeter test` — one-shot capture + OCR; saves the captured
  window and the cropped/preprocessed regions to `debug\` so you can see
  exactly what tesseract sees.
- `python -m goldmeter --debug` — keeps saving region crops while running.
- Capture is tried via `PrintWindow` first (works while the game is partly
  covered). If the game hands back a black frame — some hardware-rendered
  windows do — the meter falls back to copying the window's rectangle off
  the screen, which requires the game window to stay visible (not minimized
  and not fully covered).
- Misreads flicker but don't corrupt stats: a stage change or an implausible
  gold jump must appear on two consecutive polls before it's accepted, and
  "gold earned" is the sum of positive deltas, so spending gold mid-stage
  doesn't subtract from it.
- Stages with less than 30 seconds of total time sort to the bottom of the
  ranking — a few seconds of data would produce meaningless gold/min.
- Poll rate, OCR upscale factor and window title live in `config.json`.

## Project layout

```
goldmeter/
  capture.py    # find + grab the game window via Win32 (PrintWindow/BitBlt)
  ocr.py        # preprocessing (upscale/Otsu binarize) + tesseract + parsers
  tracker.py    # per-stage gold state machine with misread debouncing
  ranking.py    # all-time per-stage gold/min ranking from session logs
  calibrate.py  # drag-to-select OCR regions with live preview
  ui.py         # the stats window (tkinter, worker thread for OCR)
  config.py     # config.json load/save
tests/          # .venv\Scripts\python.exe -m unittest discover tests
```
