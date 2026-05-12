# Band-Restricted Ruler Calibration

## Problem

`crab_pipeline.calibrate_scale` produces inconsistent and sometimes wrong
`pixels_per_mm` values on dual-scale rulers (metric ticks on one edge, imperial
on the other). The current implementation in `_calibrate_along_axis` collapses
the entire warped ruler patch into a single 1-D tick signal via
`b_inv.sum(axis=0)`, mixing both scales together. Symptoms observed on
`Sample.Dungeness.Measurement_Photo_*` images:

- Major-tick (red) lines are unevenly spaced rather than landing on cm marks.
- `minors_per_major` is misclassified (e.g. `16` / imperial on a ruler where the
  metric scale should win).
- Reported `pixels_per_mm` drifts because minor-tick spacing is averaged across
  two different physical scales.

The image dataset contains a mix of single-scale (metric-only) and dual-scale
rulers, captured at modest resolution (768x1024). Any fix must not regress
single-scale behavior.

## Goals

- Recover correct `minors_per_major` and `pixels_per_mm` on dual-scale rulers
  without changing how images are captured.
- Preserve correctness on single-scale rulers the current algorithm already
  handles.
- No new third-party dependencies.
- Keep public APIs (`detect_ruler`, `calibrate_scale`, `ScaleInfo`) stable so
  `crab_measurebot_2000.py` continues to work with minor additions only.

## Non-Goals

- OCR-based digit anchoring.
- Manual click-through fallback in the marimo UI.
- Switching to long-tick-anchored calibration (Approach B from brainstorming).
- Building a regression test harness. Verification is visual via the marimo
  notebook.

## Approach: Band-Restricted Tick Analysis

Instead of integrating the binarised warped patch over its full perpendicular
extent, run the existing tick analysis on multiple overlapping bands of the
patch and keep the band whose result scores highest under the existing
`quality` metric. On a dual-scale ruler, one band lands on the metric tick row
and one lands on the imperial tick row; their results are scored independently
and the cleaner periodic pattern wins. On a single-scale ruler, the full-patch
band (included as one of the candidates) wins or ties, preserving today's
behavior.

### New internal helper

```python
def _calibrate_banded(b_inv: np.ndarray, axis: int) -> dict | None:
    """Run _calibrate_along_axis on multiple bands of b_inv; return the
    best-scoring result, or None if every band failed.

    The returned dict has the same keys as _calibrate_along_axis plus:
      - band: tuple[int, int]   inclusive-exclusive slice on the perpendicular axis
      - confidence_note: str | None   set when top-2 bands disagree on px/mm
    """
```

Band layout, given perpendicular dimension `P`:

- 4 sliding bands of width `floor(0.4 * P)` at stride `0.2 * P`, starting at
  offsets `0, 0.2 * P, 0.4 * P, 0.6 * P`. Together they cover `0-40%`,
  `20-60%`, `40-80%`, `60-100%` of `P`.
- 1 full-patch band `(0, P)` always included so single-scale rulers cannot
  regress vs. today.
- Skip any sliding band whose width is `< 8` px. The full-patch band is always
  attempted regardless of `P`, matching today's behavior on thin patches.

For each surviving band, slice `b_inv` along the perpendicular axis and call
the unmodified `_calibrate_along_axis(band_slice, axis)`. Track the
`(start, end)` offsets alongside the returned dict.

Select the band whose returned dict has the highest `quality`. Compute the
confidence note as follows:

- Extract the unit-decision logic currently inline in `calibrate_scale`
  (`minors_per_major -> (unit, mm_per_minor)`) into a new module-level helper
  `_decide_unit(minors_per_major) -> tuple[str, float]`. Call it from both
  `calibrate_scale` and from `_calibrate_banded` when computing the confidence
  note. This avoids duplication.
- Within `_calibrate_banded`, for the top-2 bands by quality, compute
  `pixels_per_mm = dx_minor / _decide_unit(minors_per_major)[1]`. If there are
  fewer than 2 successful bands, `confidence_note = None`. If the two px/mm
  values differ by more than 25% (relative to the winner), set
  `confidence_note = "low confidence: top bands disagree on px/mm"`; otherwise
  `None`.

### Wiring into existing functions

**`calibrate_scale(ruler_roi)`** (`crab_pipeline.py`):

- Replace the existing loop that calls `_calibrate_along_axis(b_inv, axis)`
  for `axis in (0, 1)` with a loop that calls `_calibrate_banded(b_inv, axis)`.
- Pick the higher-quality result across the two axes, same logic as today.
- Surface the band offset and confidence note on `ScaleInfo` (see below).

**`detect_ruler(bgr_img)`** (`crab_pipeline.py`):

- The candidate-scoring loop currently calls `_calibrate_along_axis(b_inv, axis)`
  to score each ruler candidate. Replace those calls with `_calibrate_banded`
  too. This keeps ROI selection and final calibration scored under the same
  metric.

### `ScaleInfo` additions

Add two new optional fields with safe defaults:

```python
@dataclass
class ScaleInfo:
    # ... existing fields unchanged ...
    winning_band: tuple[int, int] | None = None
    confidence_note: str | None = None
```

Both default to `None` so any caller that constructs or pattern-matches on
`ScaleInfo` continues to work.

## Edge Cases

- **Thin warped patch.** Sliding bands with width `< 8` px are skipped. The
  full-patch band is always attempted, matching today's behavior on thin
  patches.
- **All bands return `None` from `_calibrate_along_axis`.** `_calibrate_banded`
  returns `None`. The caller surfaces the existing
  `RuntimeError("Could not find regular tick peaks on ruler in either
  orientation")` exactly as today.
- **Cross-band unit disagreement.** No voting. The single highest-`quality`
  band wins. The existing `hierarchy_bonus` in `_calibrate_along_axis` already
  biases toward known subdivisions (8/10/16) and serves as the tie-breaker.
- **Detection vs. calibration drift.** Because both code paths now use
  `_calibrate_banded`, a ROI that wins selection will calibrate under the same
  metric. No "wins selection, loses calibration" surprises.

## Performance

Per ruler candidate during `detect_ruler`: up to 5 bands x 2 axes x 6
`min_dist` sweeps in `_calibrate_along_axis` = 60 peak-finding passes. Each
pass is O(n) over a 1-D signal a few hundred elements long, single-digit
milliseconds. Negligible against the cost of warping and contour extraction.

## Notebook Changes (`crab_measurebot_2000.py`)

Two small additions in the scale-visualization cell (cell starting at
`crab_measurebot_2000.py:90`):

1. If `scale.winning_band is not None`, draw a thin gray rectangle on the
   warped-patch overlay marking the band that won. Helps debugging which slice
   the calibration came from.
2. Append `scale.confidence_note` (when set) to the cell's title string so
   low-confidence calibrations are visible at a glance.

No changes to the measurements table cell or any other notebook cell.

## Verification

Verification is visual via the marimo notebook. For each image in `data/`:

1. Open the notebook and select the image via the dropdown.
2. Check the scale-visualization cell:
   - Red "major" lines land evenly on cm marks (metric) or half-inch marks
     (imperial).
   - `pixels_per_mm` is consistent with the visible ruler (a 1 cm span on the
     ruler should be `~10 * pixels_per_mm` pixels wide in the warped patch).
   - Reported `unit` matches the printed ruler.
   - The highlighted winning band overlaps the ruler's actual tick row, not the
     digit row or the patch edge.
3. Note any image where `confidence_note` is set; manually verify the result.

Before/after comparison: run through every image in `data/` (15 files) with the
current code, screenshot the scale cell, then re-run after the change and
compare. Acceptance criterion: no image regresses, and the dual-scale rulers
that were previously wrong now show evenly-spaced major lines and correct
units.

## Out of Scope

- Automated regression tests (no existing test harness in repo; visual
  verification is sufficient for this change).
- Manual calibration fallback UI.
- OCR / digit-anchored calibration.
- Long-tick-anchored calibration (Approach B).
- Changes to eye-blob detection, tray masking, or pairing.

## Files Touched

- `crab_pipeline.py` - add `_decide_unit` helper, add `_calibrate_banded`, wire
  both into `detect_ruler` and `calibrate_scale`, add two optional fields to
  `ScaleInfo`.
- `crab_measurebot_2000.py` - small additions to the scale-visualization cell
  for the winning-band overlay and confidence-note title.
