# Band-Restricted Ruler Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix inconsistent major/minor tick detection on dual-scale (metric+imperial) rulers by running tick analysis on overlapping bands of the warped patch and picking the band whose tick signal is cleanest.

**Architecture:** Introduce a new internal helper `_calibrate_banded(b_inv, axis)` that slices the binarised warped ruler patch into 4 overlapping bands along the perpendicular axis, plus 1 full-patch band, runs the existing `_calibrate_along_axis` on each, and returns the highest-quality result. Wire it into both `calibrate_scale` (for the final calibration) and `detect_ruler` (for candidate ROI scoring) so the two stages agree on which ruler signal is strongest. Extract the metric/imperial unit-decision logic into a reusable `_decide_unit` helper. Add two optional fields to `ScaleInfo` (`winning_band`, `confidence_note`) for visualization and confidence reporting.

**Tech Stack:** Python 3.13, OpenCV (`opencv-python>=4.10`), NumPy (`numpy>=2.0`), Matplotlib, marimo, `uv` for environment management. Spec: `docs/superpowers/specs/2026-05-12-band-restricted-ruler-calibration-design.md`.

---

## Preamble: Baseline Commit

Before starting, the source files `crab_pipeline.py`, `crab_measurebot_2000.py`, and `_smoketest.py` are present but untracked in git. Each task's commit step below uses `git add` to include the modified file, which on the first task touching a file will commit the *entire* file as a new file. If you want clean per-task diffs in code review, commit these as a baseline first:

```bash
git add crab_pipeline.py crab_measurebot_2000.py _smoketest.py
git commit -m "chore: commit baseline source files before refactor"
```

This is optional. If skipped, the first task touching each file will be the file's initial commit.

---

## Task 1: Extract `_decide_unit` Helper

**Files:**
- Modify: `crab_pipeline.py` (the unit-decision block inside `calibrate_scale`, currently around lines 404-416)

Pure refactor — no behavior change. We move the metric/imperial branch out of `calibrate_scale` into a module-level helper so `_calibrate_banded` (added in Task 4) can reuse it without duplicating logic.

- [ ] **Step 1: Add `_decide_unit` helper near the top of the Stage 2 section**

Insert immediately above the existing `def _find_peaks_1d(...)` function in `crab_pipeline.py`:

```python
def _decide_unit(minors_per_major: int) -> tuple[str, float]:
    """Map a minors-per-major count to (unit_name, mm_per_minor).

    10 -> metric (1 mm per minor); 8 or 16 -> imperial (1/8 or 1/16 inch per minor).
    Falls back to metric mm when the count is outside known ranges.
    """
    if minors_per_major in (8, 16):
        return "imperial", 25.4 / minors_per_major
    if 7 <= minors_per_major <= 12 or minors_per_major == 5:
        return "metric", 1.0
    if 13 <= minors_per_major <= 24:
        return "imperial", 25.4 / minors_per_major
    return "metric", 1.0
```

- [ ] **Step 2: Replace the inline unit-decision block in `calibrate_scale` with a call to `_decide_unit`**

Find this block (currently around `crab_pipeline.py:404-416`):

```python
    # Decide unit from minors-per-major. 10 -> metric (1 mm per minor),
    # 8 or 16 -> imperial (1/8 or 1/16 inch per minor).
    if minors_per_major in (8, 16):
        unit = "imperial"
        mm_per_minor = 25.4 / minors_per_major
    elif 7 <= minors_per_major <= 12 or minors_per_major in (5,):
        unit = "metric"
        mm_per_minor = 1.0
    elif 13 <= minors_per_major <= 24:
        unit = "imperial"
        mm_per_minor = 25.4 / minors_per_major
    else:
        # Fallback: assume metric mm
        unit = "metric"
        mm_per_minor = 1.0
```

Replace with:

```python
    unit, mm_per_minor = _decide_unit(minors_per_major)
```

- [ ] **Step 3: Verify the refactor doesn't change pipeline output**

Run: `uv run python _smoketest.py`

Expected: each image prints `scale: tick_axis=..., X.XX px/mm, metric/imperial, minors/major=N, calib=Y.Ymm`. Numbers should be identical to before the refactor (this is a pure refactor). If you ran the smoke test before Task 1, save the output and diff; if not, just confirm no exceptions and the values look reasonable.

- [ ] **Step 4: Commit**

```bash
git add crab_pipeline.py
git commit -m "refactor: extract _decide_unit helper from calibrate_scale"
```

---

## Task 2: Add Optional Fields to `ScaleInfo`

**Files:**
- Modify: `crab_pipeline.py` (the `ScaleInfo` dataclass, currently at lines 26-36)

- [ ] **Step 1: Add two optional fields to `ScaleInfo`**

Find the existing dataclass:

```python
@dataclass
class ScaleInfo:
    pixels_per_mm: float
    unit: str                   # "metric" or "imperial"
    minors_per_major: int
    tick_axis: int              # 0 = x-axis of warped patch, 1 = y-axis
    calib_segment_warped: tuple
    calib_segment_orig: tuple
    calib_mm: float
    tick_positions: list = field(default_factory=list)
    tick_lengths: list = field(default_factory=list)
```

Add two new fields at the end:

```python
@dataclass
class ScaleInfo:
    pixels_per_mm: float
    unit: str                   # "metric" or "imperial"
    minors_per_major: int
    tick_axis: int              # 0 = x-axis of warped patch, 1 = y-axis
    calib_segment_warped: tuple
    calib_segment_orig: tuple
    calib_mm: float
    tick_positions: list = field(default_factory=list)
    tick_lengths: list = field(default_factory=list)
    winning_band: tuple | None = None        # (start, end) in perpendicular axis, or None if full-patch
    confidence_note: str | None = None       # human-readable note when top bands disagree
```

- [ ] **Step 2: Verify dataclass still works**

Run: `uv run python -c "import crab_pipeline as cp; s = cp.ScaleInfo(pixels_per_mm=1.0, unit='metric', minors_per_major=10, tick_axis=0, calib_segment_warped=((0,0),(1,1)), calib_segment_orig=((0,0),(1,1)), calib_mm=10.0); print(s.winning_band, s.confidence_note)"`

Expected output: `None None`

- [ ] **Step 3: Run smoke test to confirm no regression**

Run: `uv run python _smoketest.py`

Expected: still passes for every image (callers of `ScaleInfo` don't yet set the new fields, but defaults make them optional).

- [ ] **Step 4: Commit**

```bash
git add crab_pipeline.py
git commit -m "feat: add winning_band and confidence_note fields to ScaleInfo"
```

---

## Task 3: Add `_calibrate_banded` Helper

**Files:**
- Modify: `crab_pipeline.py` (add new helper after `_calibrate_along_axis`, currently ends around line 378)

This is the core new logic. Insert `_calibrate_banded` immediately after the `_calibrate_along_axis` function and before `def calibrate_scale(...)`.

- [ ] **Step 1: Write `_calibrate_banded`**

Insert this function in `crab_pipeline.py` right after the closing `}` line of `_calibrate_along_axis` (the `return { "quality": ..., ... }` block) and before `def calibrate_scale(...)`:

```python
def _calibrate_banded(b_inv: np.ndarray, axis: int):
    """Run _calibrate_along_axis on multiple bands of b_inv; return best result.

    On a dual-scale ruler (metric ticks on one edge, imperial on the other), the
    full-patch column sum mixes both scales into one signal and produces garbage
    period estimates. We slice b_inv into 4 overlapping bands along the axis
    perpendicular to the ticks, plus 1 full-patch band, and pick whichever band's
    result has the highest `quality` score. On single-scale rulers the full-patch
    band typically wins or ties, preserving today's behavior.

    Returns the chosen result dict from _calibrate_along_axis with two extra keys:
      - band: tuple[int, int]    (start, end) on the perpendicular axis
      - confidence_note: str|None  set when top-2 bands disagree on px/mm by >25%
    Returns None if every band returned None.
    """
    perp = b_inv.shape[0] if axis == 0 else b_inv.shape[1]
    band_width = int(0.4 * perp)

    # Build candidate band slices: 4 sliding at 20% stride + 1 full-patch.
    candidate_bands: list[tuple[int, int]] = []
    for i in range(4):
        start = int(i * 0.2 * perp)
        end = start + band_width
        if end > perp:
            end = perp
        if end - start >= 8:
            candidate_bands.append((start, end))
    candidate_bands.append((0, perp))  # full-patch always

    # Deduplicate (e.g. if perp is small, the last sliding band may equal full-patch).
    seen = set()
    unique_bands = []
    for b in candidate_bands:
        if b not in seen:
            seen.add(b)
            unique_bands.append(b)

    results: list[tuple[float, tuple[int, int], dict]] = []
    for start, end in unique_bands:
        if axis == 0:
            band_slice = b_inv[start:end, :]
        else:
            band_slice = b_inv[:, start:end]
        r = _calibrate_along_axis(band_slice, axis)
        if r is None:
            continue
        results.append((float(r["quality"]), (start, end), r))

    if not results:
        return None

    results.sort(key=lambda x: x[0], reverse=True)
    _quality_top, band_top, r_top = results[0]

    confidence_note: str | None = None
    if len(results) >= 2:
        _, _, r1 = results[0]
        _, _, r2 = results[1]
        _, mm_per_minor_1 = _decide_unit(int(r1["minors_per_major"]))
        _, mm_per_minor_2 = _decide_unit(int(r2["minors_per_major"]))
        pxmm1 = r1["dx_minor"] / mm_per_minor_1
        pxmm2 = r2["dx_minor"] / mm_per_minor_2
        if pxmm1 > 0 and abs(pxmm1 - pxmm2) / pxmm1 > 0.25:
            confidence_note = "low confidence: top bands disagree on px/mm"

    r_top["band"] = band_top
    r_top["confidence_note"] = confidence_note
    return r_top
```

- [ ] **Step 2: Exercise the new helper on a synthetic input to confirm it doesn't crash**

Run this one-liner:

```bash
uv run python -c "
import numpy as np
import crab_pipeline as cp
# Synthetic: a 200x80 image with vertical bars every 10 columns, only in rows 10..30.
img = np.zeros((80, 200), dtype=np.uint8)
for x in range(5, 200, 10):
    img[10:30, x] = 255
r = cp._calibrate_banded(img, axis=0)
print('result keys:', sorted(r.keys()) if r else None)
print('band:', r['band'] if r else None)
print('minors_per_major:', r['minors_per_major'] if r else None)
print('dx_minor:', round(r['dx_minor'], 2) if r else None)
"
```

Expected:
- `result keys` contains `band`, `confidence_note`, `dx_minor`, `diffs`, `lengths`, `major_indices`, `minors_per_major`, `peaks`, `quality`.
- `band` is a `(start, end)` tuple whose range overlaps `[10, 30]` (the rows with ticks).
- `dx_minor` is approximately `10.0` (the synthetic spacing).
- `minors_per_major` is some small integer (synthetic input has no length hierarchy, so the value isn't meaningful — we're just confirming no crash).

- [ ] **Step 3: Confirm the rest of the pipeline still passes**

Run: `uv run python _smoketest.py`

Expected: no exceptions, same output as before Task 3 (nothing calls `_calibrate_banded` yet — this commit only adds the function).

- [ ] **Step 4: Commit**

```bash
git add crab_pipeline.py
git commit -m "feat: add _calibrate_banded helper for multi-band tick analysis"
```

---

## Task 4: Wire `_calibrate_banded` into `calibrate_scale`

**Files:**
- Modify: `crab_pipeline.py` (the `calibrate_scale` function, currently at lines 381-461)

Replace the per-axis loop's call to `_calibrate_along_axis` with `_calibrate_banded`, and surface the band + confidence note on the returned `ScaleInfo`.

- [ ] **Step 1: Update the per-axis loop**

Find this block at the top of `calibrate_scale`:

```python
    results = []
    for axis in (0, 1):
        r = _calibrate_along_axis(b_inv, axis)
        if r is not None:
            results.append((axis, r))
```

Replace with:

```python
    results = []
    for axis in (0, 1):
        r = _calibrate_banded(b_inv, axis)
        if r is not None:
            results.append((axis, r))
```

- [ ] **Step 2: Pass new fields into the returned `ScaleInfo`**

Find the final `return ScaleInfo(...)` at the end of `calibrate_scale`. It currently looks like:

```python
    return ScaleInfo(
        pixels_per_mm=pixels_per_mm,
        unit=unit,
        minors_per_major=minors_per_major,
        tick_axis=tick_axis,
        calib_segment_warped=seg_warped,
        calib_segment_orig=(
            (float(seg_orig[0, 0]), float(seg_orig[0, 1])),
            (float(seg_orig[1, 0]), float(seg_orig[1, 1])),
        ),
        calib_mm=calib_mm,
        tick_positions=[int(p) for p in peaks],
        tick_lengths=[float(_l) for _l in lengths],
    )
```

Just above it, extract the band info from the chosen result dict `r` (which now carries `band` and `confidence_note`). Modify the return:

```python
    winning_band = r.get("band")
    confidence_note = r.get("confidence_note")
    return ScaleInfo(
        pixels_per_mm=pixels_per_mm,
        unit=unit,
        minors_per_major=minors_per_major,
        tick_axis=tick_axis,
        calib_segment_warped=seg_warped,
        calib_segment_orig=(
            (float(seg_orig[0, 0]), float(seg_orig[0, 1])),
            (float(seg_orig[1, 0]), float(seg_orig[1, 1])),
        ),
        calib_mm=calib_mm,
        tick_positions=[int(p) for p in peaks],
        tick_lengths=[float(_l) for _l in lengths],
        winning_band=winning_band,
        confidence_note=confidence_note,
    )
```

- [ ] **Step 3: Run the smoke test to see how every image calibrates with banded analysis**

Run: `uv run python _smoketest.py`

Expected: every image still produces a scale result. Some images' `px/mm` and `minors/major` may now differ from before (this is the intended fix). No images should now FAIL that previously succeeded. If any image now raises `RuntimeError("Could not find regular tick peaks...")` that previously worked, that's a regression — diagnose before committing.

- [ ] **Step 4: Spot-check via the marimo notebook on one previously broken image**

Run: `uv run marimo edit crab_measurebot_2000.py`

In the notebook, select `Sample.Dungeness.Measurement_Photo_1.JPEG` (or whichever image you previously screenshot with the off-base imperial calibration). Look at the scale-visualization cell:
- Red "major" lines should now be evenly spaced and land on cm marks.
- `minors_per_major` should show `10` and unit `metric` (for the metric-dominant rulers).

Don't worry about visualizing the winning band yet — that comes in Task 6.

- [ ] **Step 5: Commit**

```bash
git add crab_pipeline.py
git commit -m "feat: use banded tick analysis in calibrate_scale"
```

---

## Task 5: Wire `_calibrate_banded` into `detect_ruler`

**Files:**
- Modify: `crab_pipeline.py` (the candidate-scoring loop inside `detect_ruler`, currently around lines 199-215)

`detect_ruler` scores each ruler-rectangle candidate by running calibration and taking the quality. The scoring must match what `calibrate_scale` will use, so we switch both to `_calibrate_banded`.

- [ ] **Step 1: Replace the per-candidate scoring loop**

Find this block inside `detect_ruler`:

```python
    best = None
    gray_full = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
    for quad, long_edge, short_edge in candidates:
        out_w = int(round(long_edge))
        out_h = int(round(short_edge))
        if out_w < 40 or out_h < 40:
            continue
        warped_gray, _ = _warp_quad(gray_full, quad, out_w, out_h)
        warped_bgr, _ = _warp_quad(bgr_img, quad, out_w, out_h)
        _, b_inv = cv2.threshold(warped_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        best_axis_quality = 0.0
        for axis in (0, 1):
            r = _calibrate_along_axis(b_inv, axis)
            if r is not None:
                best_axis_quality = max(best_axis_quality, r["quality"])
        if best is None or best_axis_quality > best[0]:
            best = (best_axis_quality, quad, warped_gray, warped_bgr)
```

Replace with (only the `for axis in (0, 1)` inner loop changes):

```python
    best = None
    gray_full = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
    for quad, long_edge, short_edge in candidates:
        out_w = int(round(long_edge))
        out_h = int(round(short_edge))
        if out_w < 40 or out_h < 40:
            continue
        warped_gray, _ = _warp_quad(gray_full, quad, out_w, out_h)
        warped_bgr, _ = _warp_quad(bgr_img, quad, out_w, out_h)
        _, b_inv = cv2.threshold(warped_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        best_axis_quality = 0.0
        for axis in (0, 1):
            r = _calibrate_banded(b_inv, axis)
            if r is not None:
                best_axis_quality = max(best_axis_quality, float(r["quality"]))
        if best is None or best_axis_quality > best[0]:
            best = (best_axis_quality, quad, warped_gray, warped_bgr)
```

- [ ] **Step 2: Run the smoke test**

Run: `uv run python _smoketest.py`

Expected: every image still produces a ruler ROI and a scale. If any image now fails ruler detection that previously worked, the banded-scoring metric is rejecting a candidate the collapsed metric was accepting — diagnose before committing. (This shouldn't happen because the full-patch band is always one of the bands considered, so the banded score is `>=` the collapsed score, but verify.)

- [ ] **Step 3: Commit**

```bash
git add crab_pipeline.py
git commit -m "feat: use banded tick analysis in detect_ruler candidate scoring"
```

---

## Task 6: Update Marimo Notebook Visualization

**Files:**
- Modify: `crab_measurebot_2000.py` (the scale-visualization cell, currently at lines 89-112)

Two small additions: draw the winning band as a translucent overlay on the warped patch, and append the confidence note (if any) to the cell title.

- [ ] **Step 1: Update the scale-visualization cell**

Find the cell starting around `crab_measurebot_2000.py:89`:

```python
@app.cell
def _(cp, cv2, plt, ruler_roi):
    scale = cp.calibrate_scale(ruler_roi)

    fig_scale, ax_scale = plt.subplots(figsize=(12, 4))
    warped_vis = cv2.cvtColor(ruler_roi.warped_bgr, cv2.COLOR_BGR2RGB).copy()
    _max_len = max(scale.tick_lengths) if scale.tick_lengths else 1.0
    for p, _l in zip(scale.tick_positions, scale.tick_lengths):
        is_major = _l > 0.6 * _max_len
        color = (255, 0, 0) if is_major else (0, 200, 0)
        if scale.tick_axis == 0:
            cv2.line(warped_vis, (p, 0), (p, warped_vis.shape[0] - 1), color, 1)
        else:
            cv2.line(warped_vis, (0, p), (warped_vis.shape[1] - 1, p), color, 1)
    p1, p2 = scale.calib_segment_warped
    cv2.line(warped_vis, p1, p2, (255, 255, 0), 3)
    ax_scale.imshow(warped_vis)
    ax_scale.set_title(
        f"{scale.unit}: {scale.minors_per_major} minors/major, "
        f"{scale.pixels_per_mm:.2f} px/mm, calib segment = {scale.calib_mm:.1f} mm"
    )
    ax_scale.axis("off")
    fig_scale
    return (scale,)
```

Replace with:

```python
@app.cell
def _(cp, cv2, plt, ruler_roi):
    scale = cp.calibrate_scale(ruler_roi)

    fig_scale, ax_scale = plt.subplots(figsize=(12, 4))
    warped_vis = cv2.cvtColor(ruler_roi.warped_bgr, cv2.COLOR_BGR2RGB).copy()
    _max_len = max(scale.tick_lengths) if scale.tick_lengths else 1.0
    for p, _l in zip(scale.tick_positions, scale.tick_lengths):
        is_major = _l > 0.6 * _max_len
        color = (255, 0, 0) if is_major else (0, 200, 0)
        if scale.tick_axis == 0:
            cv2.line(warped_vis, (p, 0), (p, warped_vis.shape[0] - 1), color, 1)
        else:
            cv2.line(warped_vis, (0, p), (warped_vis.shape[1] - 1, p), color, 1)
    p1, p2 = scale.calib_segment_warped
    cv2.line(warped_vis, p1, p2, (255, 255, 0), 3)

    # Show which band the calibration came from.
    if scale.winning_band is not None:
        _b_start, _b_end = scale.winning_band
        if scale.tick_axis == 0:
            cv2.rectangle(warped_vis, (0, _b_start), (warped_vis.shape[1] - 1, _b_end - 1), (200, 200, 200), 2)
        else:
            cv2.rectangle(warped_vis, (_b_start, 0), (_b_end - 1, warped_vis.shape[0] - 1), (200, 200, 200), 2)

    ax_scale.imshow(warped_vis)
    _title = (
        f"{scale.unit}: {scale.minors_per_major} minors/major, "
        f"{scale.pixels_per_mm:.2f} px/mm, calib segment = {scale.calib_mm:.1f} mm"
    )
    if scale.confidence_note:
        _title += f"  [{scale.confidence_note}]"
    ax_scale.set_title(_title)
    ax_scale.axis("off")
    fig_scale
    return (scale,)
```

- [ ] **Step 2: Launch the notebook and confirm rendering**

Run: `uv run marimo edit crab_measurebot_2000.py`

In the notebook:
- Select any image. The scale cell should render the warped patch with the existing red/green tick lines, the yellow calibration segment, *and* a gray rectangle outlining the band the calibration came from.
- If the calibration is confident (no note), the title is unchanged from today's format.
- If `confidence_note` is set, it appears in brackets at the end of the title.

- [ ] **Step 3: Commit**

```bash
git add crab_measurebot_2000.py
git commit -m "feat: show winning band overlay and confidence note in marimo scale cell"
```

---

## Task 7: Visual Verification Across All Images

This task has no code changes. It documents the acceptance check.

- [ ] **Step 1: Launch the notebook**

Run: `uv run marimo edit crab_measurebot_2000.py`

- [ ] **Step 2: Step through every image in `data/`**

Use the dropdown to select each of the 15 `Sample.Dungeness.Measurement_Photo_*.JPEG` files in turn. For each, confirm in the scale-visualization cell:

1. **Red "major" lines** land evenly on cm marks (metric) or half-inch marks (imperial). Visual count of intervals between adjacent red lines should match the ruler.
2. **Reported unit** (`metric` / `imperial`) matches what is printed on the ruler in the image.
3. **`pixels_per_mm`** is plausible: pick a 1 cm span on the ruler in the patch, eyeball its width in pixels, divide by 10 — should match the reported value within a few percent.
4. **Winning-band gray rectangle** overlaps the ruler's actual tick row, not the digit row or the patch edge.
5. **`confidence_note`** appears only on images where the calibration is genuinely ambiguous (a small fraction of the dataset, if any).

- [ ] **Step 3: Document any regressions or unresolved failures**

If any image now produces a *worse* result than before this change (e.g. the previous calibration was correct and this one isn't), note the filename and what went wrong. Two likely causes:

- A single-scale image where the full-patch band lost on quality to a sliding band that picked up a noisy edge → diagnose by widening the sliding bands or excluding bands that overlap the patch perimeter.
- A dual-scale image where the *wrong* tick row (the noisier of the two) won → likely the `hierarchy_bonus` on `minors_per_major=8/10/16` is doing its job for the right band but the wrong band has stronger raw peak quality; consider raising `hierarchy_bonus`.

If no regressions and the previously-broken dual-scale rulers (e.g. `Sample.Dungeness.Measurement_Photo_1.JPEG`) now show even cm-spaced red lines with `minors_per_major=10`, the change is complete.

- [ ] **Step 4: Commit a short verification note (optional)**

If you tracked any noteworthy observations, add them as a markdown note alongside the spec, e.g. `docs/superpowers/specs/2026-05-12-band-restricted-ruler-calibration-results.md`, and commit. Otherwise, no commit needed for this task.

---

## Summary of Files Touched

- `crab_pipeline.py`: `_decide_unit` (new helper), `_calibrate_banded` (new helper), two new optional fields on `ScaleInfo`, modified candidate-scoring loop in `detect_ruler`, modified `calibrate_scale` to call `_calibrate_banded` and surface band/confidence info.
- `crab_measurebot_2000.py`: scale-visualization cell updated to draw the winning-band rectangle and append `confidence_note` to the title.

No new files, no new dependencies, no test infrastructure required.
