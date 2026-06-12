# TBH Gold Meter

A screen reader (OCR, display-only — no audio) for **TBH: Task Bar Hero**.
It captures the game window, reads the gold counter and the stage label off
the screen, and shows live stats in its own small window:

- current stage and current gold
- **gold earned on the current stage**, time on stage, gold/min
- session totals and a per-stage history table
- every finished stage is appended to `sessions/session-*.jsonl`

It never touches the game's memory or files — it only looks at pixels.

## Requirements

- Linux with the game running through Steam/Proton (the game window runs on
  XWayland, which is how the meter captures it without Wayland permission
  prompts). GNOME Wayland is fine.
- System packages (one-time):

  ```bash
  sudo pacman -S --needed tesseract tesseract-data-eng tk
  ```

- Python deps (already vendored in `.venv/` if you ran `setup.sh`):

  ```bash
  ./setup.sh
  ```

## Usage

1. **Start the game.**
2. Check the meter can see it:

   ```bash
   .venv/bin/python -m goldmeter windows
   ```

   The game's title should appear with a `*`. If the title isn't matched,
   edit `window_title` in `config.json` to a substring of the real title.
3. **Calibrate once** (drag a box around the gold number, Enter, then around
   the stage number, Enter — the OCR preview shows what it reads live):

   ```bash
   .venv/bin/python -m goldmeter calibrate
   ```

4. **Run the meter:**

   ```bash
   .venv/bin/python -m goldmeter
   ```

### Troubleshooting

- `python -m goldmeter test` — one-shot capture + OCR; saves the captured
  window and the cropped/preprocessed regions to `debug/` so you can see
  exactly what tesseract sees.
- `python -m goldmeter --debug` — keeps saving region crops while running.
- Misreads flicker but don't corrupt stats: a stage change or an implausible
  gold jump must appear on two consecutive polls before it's accepted, and
  "gold earned" is the sum of positive deltas, so spending gold mid-stage
  doesn't subtract from it.
- Poll rate, OCR upscale factor and window title live in `config.json`.

## Project layout

```
goldmeter/
  capture.py    # find + grab the game window via X11/XWayland
  ocr.py        # preprocessing (upscale/Otsu binarize) + tesseract + parsers
  tracker.py    # per-stage gold state machine with misread debouncing
  calibrate.py  # drag-to-select OCR regions with live preview
  ui.py         # the stats window (tkinter, worker thread for OCR)
  config.py     # config.json load/save
tests/          # .venv/bin/python -m unittest discover tests
```
