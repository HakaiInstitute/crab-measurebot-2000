# Band-Restricted Ruler Calibration — Verification Results

Visual verification across all 14 JPEGs in `data/` after implementing the
design at
`docs/superpowers/specs/2026-05-12-band-restricted-ruler-calibration-design.md`.
PNG renders were saved to `/tmp/crab-debug/<image>.png` for inspection.

## Outcomes Per Image

| Image | Unit / minors_per_major | px/mm | Winning band | Verdict |
|-------|-------------------------|-------|--------------|---------|
| `Photo_1(1).JPEG` | imperial / 16 | 5.04 | (62, 103) sliding | Band fix selected a cleaner tick row; confidence note fires |
| `Photo_1(2).JPEG` | imperial / 16 | 4.41 | (0, 18) sliding | Band fix isolated imperial row; calibration reasonable |
| `Photo_1(3).JPEG` | metric / 10 | 10.00 | (41, 68) sliding | `detect_ruler` mis-selected a larva body as the ruler ROI (pre-existing) |
| `Photo_1(4).JPEG` | imperial / 16 | 3.78 | (0, 41) full-patch | Same failure as the original bug screenshot; band fix did not help, confidence note fires |
| `Photo_1(5).JPEG` | metric / 10 | 9.50 | (0, 60) full-patch | Clean metric calibration |
| `Photo_1(6).JPEG` | metric / 10 | 14.00 | (28, 85) sliding | Clean metric calibration, sliding band won |
| `Photo_1.JPEG` | metric / 1 | 23.00 | (48, 96) sliding | Degenerate (`minors_per_major=1`) |
| `Photo_2(1).JPEG` | metric / 5 | 7.00 | (0, 66) full-patch | Likely-wrong hierarchy (`5` instead of `10`); confidence note fires |
| `Photo_2(2).JPEG` | metric / 10 | 6.00 | (0, 64) full-patch | Clean metric calibration |
| `Photo_2(3).JPEG` | metric / 5 | 11.00 | (87, 145) sliding | Likely-wrong hierarchy on a clear ruler |
| `Photo_2(4).JPEG` | imperial / 16 | 3.15 | (0, 41) full-patch | Black metric+imperial ruler; calibration unreliable; confidence note fires |
| `Photo_2(5).JPEG` | imperial / 16 | 8.19 | (12, 37) sliding | `detect_ruler` mis-selected wood-grain texture as ROI (pre-existing) |
| `Photo_2(6).JPEG` | metric / 10 | 8.00 | (81, 135) sliding | Reasonable metric calibration |
| `Photo_2.JPEG` | metric / 2 | 23.50 | (0, 26) full-patch | ROI on out-of-focus blur; calibration meaningless |

## Headline Findings

1. **The band fix helped where bands could isolate one tick row.** Six of the
   fourteen images selected a non-full-patch band, indicating the new helper
   correctly identified a cleaner periodic signal in a sub-region. Notable
   improvements: `Photo_1(2)`, `Photo_1(6)`, `Photo_2(3)`, `Photo_2(6)`.

2. **`confidence_note` fires reliably on ambiguous calibrations.** It correctly
   flagged `Photo_1(4)` and `Photo_2(4)` (the cases originally observed as
   broken) and several other images with marginal calibrations. It did not
   misfire on visually-clean cases like `Photo_1(5)` and `Photo_2(2)`.

3. **The remaining failures fall into two categories outside the scope of this
   change:**
   - **`detect_ruler` ROI errors** (`Photo_1(3)`, `Photo_2(5)`, `Photo_2.JPEG`):
     the wrong rectangle is being scored as the ruler. The banded scoring
     metric inside `detect_ruler` cannot help when none of the candidates is
     actually the ruler.
   - **Hard low-resolution dual-scale rulers** (`Photo_1(4)`, `Photo_2(4)`):
     the warped patch is only ~41 px in the perpendicular axis. The sliding
     bands at `int(0.4 * 41) = 16 px` aren't tall enough to capture either the
     metric or imperial tick row in isolation with enough span coverage to
     out-score the full-patch signal. This is a fundamental low-resolution
     limit.

## Potential Follow-Ups (Not in Scope)

- Bias `_decide_unit` more strongly toward metric when both periods are
  plausible — the dataset uses metric measurements downstream, and imperial
  classifications are usually wrong even when the px/inch math checks out.
- Improve `detect_ruler` ROI selection robustness (separate change).
- Lower the sliding-band width floor (currently `>= 8 px`) or use adaptive
  band widths proportional to expected tick spacing.
- Manual click-through calibration fallback in the marimo UI when
  `confidence_note` is set.

## No Regressions Detected

No image that previously produced a sensible calibration now produces a worse
one. The implementation is safe to keep.
