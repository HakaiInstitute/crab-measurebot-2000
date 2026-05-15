# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

CrabMeasureBot 2000 is a PySide6 desktop GUI for manually measuring interocular distances (IOD) from crab larvae photos. Users open a directory of images, draw a scale reference line from a ruler visible in the image, then draw measurement segments between eye points. All data persists to a SQLite database (`measurebot.db`) stored in the opened image directory and can be exported to CSV.

## Commands

```bash
# Install dependencies (uses uv)
uv sync --extra dev

# Run the app
uv run crab-measurebot
# or
uv run python -m crab_measurebot_2000

# Lint
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_utils.py

# Run a single test
uv run pytest tests/test_utils.py::test_compute_distance_mm_basic
```

## Architecture

The app is split across two files:

- **`src/crab_measurebot_2000/__main__.py`** — startup: `WelcomeDialog` (directory picker), Tortoise ORM initialization, `QApplication` + `qasync` event loop setup. The DB is initialized here before `MainWindow` is created.
- **`src/crab_measurebot_2000/app.py`** — everything else: ORM models, pure utility functions, and all Qt widgets.

### Three layers in `app.py`

1. **Storage**: `Image` and `Measurement` Tortoise ORM models backed by SQLite. All coordinates are stored in original image pixels — zoom/pan state never mutates persisted values.

2. **State**: `AppState` dataclass holds the current image index, zoom, pan offsets, interaction mode (`idle` / `placing_iod` / `placing_scale` / `panning`), and the loaded DB records for the current image.

3. **UI**:
   - `ImageCanvas(QWidget)` — draws the image via `QPainter` with zoom/pan, renders overlays (scale line in yellow `#ffdd00`, IOD segments in cyan `#00eeff`), and handles all mouse/gesture input. Emits signals when segments are placed or deleted.
   - `RightPanel(QWidget)` — fixed 200px right panel showing file info, scale status, measurement list, controls reference, and export button.
   - `MainWindow(QMainWindow)` — wires canvas signals to async DB operations using `@qasync.asyncSlot`. Navigation, keyboard shortcuts, and export live here.

### Async model

Qt's event loop and Tortoise ORM's asyncio requirements are bridged with `qasync`. All DB calls happen in `async` slot handlers on `MainWindow`. Navigation cancels the in-flight `asyncio.Task` for the previous image load before starting a new one.

### Known issue

The test files (`tests/`) import from `crab_measurebot_2000.main`, but the module was renamed to `crab_measurebot_2000.app`. The `conftest.py` fixture also references `crab_measurebot_2000.main`. These imports need updating to `crab_measurebot_2000.app`.
