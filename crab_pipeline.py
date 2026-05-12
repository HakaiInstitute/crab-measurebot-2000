"""Classical-CV pipeline for measuring crab larvae interocular distance.

Stages:
1. detect_ruler(bgr) -> RulerROI
2. calibrate_scale(ruler) -> ScaleInfo
3. find_tray_mask(bgr, ruler.quad) -> mask
4. detect_eye_blobs(bgr, mask, scale) -> list[(x, y, area)]
5. pair_eyes(eyes, scale, bgr) -> list[Measurement]
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class RulerROI:
    quad: np.ndarray            # (4, 2) float32 in original image coords (TL, TR, BR, BL after rectification)
    warped_gray: np.ndarray
    warped_bgr: np.ndarray
    score: float                # rulerness score (higher = stronger periodic content)


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


@dataclass
class Measurement:
    larva_id: int
    eye1: tuple
    eye2: tuple
    distance_px: float
    distance_mm: float
    distance_cm: float


# ----------------------------------------------------------------------------
# Stage 1: Ruler detection
# ----------------------------------------------------------------------------
def _box_to_long_first_quad(box: np.ndarray):
    """Reorder boxPoints output so corners[0]->corners[1] traces the LONG edge.

    boxPoints returns 4 corners of a rotated rectangle in a consistent cyclic order
    (consecutive corners share an edge). The two edges alternate between "width" and
    "height". We choose the starting corner such that the first edge is the long one,
    giving canonical (TL, TR, BR, BL) of a wider-than-tall rectangle after warping.

    Returns: (quad_long_first, long_edge_len, short_edge_len)
    """
    box = np.asarray(box, dtype=np.float32).reshape(4, 2)
    edge01 = float(np.linalg.norm(box[1] - box[0]))
    edge12 = float(np.linalg.norm(box[2] - box[1]))
    if edge01 >= edge12:
        quad = box[[0, 1, 2, 3]]
        return quad.astype(np.float32), edge01, edge12
    quad = box[[1, 2, 3, 0]]
    return quad.astype(np.float32), edge12, edge01


def _warp_quad(img, quad, out_w, out_h):
    """Warp the 4-point quadrilateral to an axis-aligned (out_w, out_h) rectangle."""
    dst = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype=np.float32,
    )
    M = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    warped = cv2.warpPerspective(img, M, (out_w, out_h))
    return warped, M


def _find_tray_only_mask(bgr_img: np.ndarray) -> np.ndarray:
    """Bright-tray segmentation (no ruler subtraction).

    The tray is always white/light and (mostly) low-saturation, sitting on darker or
    higher-saturation backgrounds (wood, concrete, dirt). We segment it once before
    ruler detection so background textures can't propose ruler candidates.
    """
    h, w = bgr_img.shape[:2]
    hsv = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    s = hsv[:, :, 1]
    bright = ((v > 140) & (s < 90)).astype(np.uint8) * 255
    # Aggressively close to bridge larvae, ticks, and the ruler so the tray fills in.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (45, 45))
    closed = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, kernel)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    if n <= 1:
        return np.ones((h, w), dtype=np.uint8) * 255
    sizes = stats[1:, cv2.CC_STAT_AREA]
    largest = 1 + int(np.argmax(sizes))
    mask = (labels == largest).astype(np.uint8) * 255
    # If the tray didn't dominate (e.g., reference-document photos), fall back to whole frame.
    if mask.sum() / 255 < 0.10 * h * w:
        return np.ones((h, w), dtype=np.uint8) * 255
    # Fill any small interior holes.
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def _binarise_candidate_sources(gray: np.ndarray):
    """Yield masks where ruler candidates may live.

    Each mask is binarised so connected components -> rectangular candidates.
    We use complementary strategies and multiple kernel sizes so small black rulers,
    large white rulers, and rotated rulers are all caught somewhere.
    """
    # 1. Adaptive threshold (both polarities) with two close kernel sizes — catches
    #    rulers from small (~80px) to large (~700px). Small kernels for small rulers
    #    so they don't bleed into surrounding tray edges.
    for invert in (False, True):
        thr = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY,
            51,
            5,
        )
        for k in (15, 31):
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
            yield cv2.morphologyEx(thr, cv2.MORPH_CLOSE, kernel)

    # 2. Edge density — rulers are dense with tick edges regardless of ruler colour.
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    density = cv2.GaussianBlur(mag, (31, 31), 0)
    thr_val = float(np.percentile(density, 92))
    high_density = (density > thr_val).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
    yield cv2.morphologyEx(high_density, cv2.MORPH_CLOSE, kernel)

    # 3. Otsu globally inverted — catches black rulers on bright trays. Two kernels.
    _, otsu_inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    for k in (15, 25):
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
        yield cv2.morphologyEx(otsu_inv, cv2.MORPH_CLOSE, kernel)


def detect_ruler(bgr_img: np.ndarray) -> RulerROI:
    """Locate the ruler patch.

    Strategy: propose many rectangular candidates from multiple binarisation sources,
    then *attempt calibration* on each and pick whichever has the strongest
    regular-tick pattern. This is more robust than scoring on FFT alone because it
    rejects high-edge-density false positives (textured backgrounds, the tray
    itself) — those won't yield a clean periodic tick pattern.
    """
    h, w = bgr_img.shape[:2]
    gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
    gray_blur = cv2.GaussianBlur(gray, (5, 5), 0)

    # Constrain ruler search to the bright tray — kills wood/concrete/cloth false positives.
    tray_mask = _find_tray_only_mask(bgr_img)
    # Erode mask slightly so we don't pick up the curved tray rim as a "ruler".
    tray_mask_eroded = cv2.erode(tray_mask, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)))

    candidates = []
    for binmask in _binarise_candidate_sources(gray_blur):
        # Only keep candidate pixels that lie inside the tray.
        binmask = cv2.bitwise_and(binmask, binmask, mask=tray_mask_eroded)
        contours, _ = cv2.findContours(binmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            if area < 0.001 * h * w:
                continue
            rect = cv2.minAreaRect(c)
            (_cx, _cy), (rw, rh), _angle = rect
            if rw < 25 or rh < 25:
                continue
            long_side = max(rw, rh)
            short_side = min(rw, rh)
            if long_side / short_side > 5.0:
                continue
            # Cap absurd bboxes (would be the whole image / tray-as-ruler) but allow
            # most rulers including big closeups (~60% of image).
            if rw * rh > 0.65 * h * w:
                continue
            box = cv2.boxPoints(rect)
            quad, long_edge, short_edge = _box_to_long_first_quad(box)
            candidates.append((quad, long_edge, short_edge))

    if not candidates:
        raise RuntimeError("No ruler candidates found")

    # For each candidate, run calibration; the candidate that yields the strongest
    # tick pattern is the actual ruler.
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

    if best is None or best[0] <= 0:
        raise RuntimeError("No periodic structure found in any ruler candidate")

    score, quad, warped_gray, warped_bgr = best
    return RulerROI(
        quad=quad.astype(np.float32),
        warped_gray=warped_gray,
        warped_bgr=warped_bgr,
        score=score,
    )


# ----------------------------------------------------------------------------
# Stage 2: Scale calibration
# ----------------------------------------------------------------------------
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


def _find_peaks_1d(signal: np.ndarray, min_distance: int, prominence: float):
    peaks = []
    n = len(signal)
    if n < 3:
        return []
    for i in range(1, n - 1):
        if signal[i] <= signal[i - 1] or signal[i] < signal[i + 1]:
            continue
        lo = max(0, i - min_distance)
        hi = min(n, i + min_distance + 1)
        if signal[i] != signal[lo:hi].max():
            continue
        left_base = signal[lo:i].min() if i > lo else signal[i]
        right_base = signal[i + 1:hi].min() if hi > i + 1 else signal[i]
        prom = signal[i] - max(left_base, right_base)
        if prom >= prominence:
            peaks.append(i)
    return peaks


def _calibrate_along_axis(b_inv: np.ndarray, axis: int):
    """Try to calibrate ticks along the given axis of the binarised ruler.

    Returns a dict with keys (quality, peaks, diffs, lengths, dx_minor, minors_per_major,
    major_indices) or None if no plausible pattern found.
    """
    if axis == 0:
        tick_signal = b_inv.sum(axis=0).astype(np.float32)
        perp_size = b_inv.shape[0]
    else:
        tick_signal = b_inv.sum(axis=1).astype(np.float32)
        perp_size = b_inv.shape[1]

    kernel_size = max(3, perp_size // 50)
    if kernel_size % 2 == 0:
        kernel_size += 1
    smoothed = cv2.GaussianBlur(tick_signal.reshape(-1, 1), (1, kernel_size), 0).flatten()

    best_peaks = None
    for min_dist in (3, 5, 8, 12, 18, 25):
        prom = (smoothed.max() - smoothed.min()) * 0.05
        peaks = _find_peaks_1d(smoothed, min_dist, prom)
        if len(peaks) < 6:
            continue
        diffs = np.diff(peaks)
        cv_score = float(np.std(diffs) / (np.mean(diffs) + 1e-6))
        # "Quality" = many peaks + tight spacing distribution + good span coverage.
        span_frac = (peaks[-1] - peaks[0]) / max(1, len(tick_signal) - 1)
        quality = len(peaks) * span_frac / (cv_score + 0.05)
        if best_peaks is None or quality > best_peaks[0]:
            best_peaks = (quality, peaks, diffs)

    if best_peaks is None:
        return None

    quality, peaks, diffs = best_peaks
    dx_minor = float(np.median(diffs))

    # Per-tick length: how far the dark column extends perpendicular to the tick axis at that position.
    lengths = []
    if axis == 0:
        for p in peaks:
            col = b_inv[:, max(0, p - 1): p + 2].max(axis=1)
            lengths.append(int((col > 0).sum()))
    else:
        for p in peaks:
            row = b_inv[max(0, p - 1): p + 2, :].max(axis=0)
            lengths.append(int((row > 0).sum()))
    lengths = np.array(lengths, dtype=np.float32)

    # Detect tick-length periodicity. On a metric ruler the lengths repeat every 10
    # ticks (1mm, 1mm, ..., 5mm, 1mm, ..., 1cm); on imperial they repeat every 16.
    # An FFT on the length sequence reveals this period far more reliably than
    # an Otsu threshold (which can be fooled by 3-level hierarchies).
    minors_per_major = 1
    if lengths.max() > lengths.min() and len(lengths) >= 8:
        centred = lengths - lengths.mean()
        spec = np.abs(np.fft.rfft(centred))
        spec[0] = 0  # discard DC
        # Frequency k corresponds to period N/k where N = len(lengths).
        # We are interested in periods between 4 and 30 ticks per major.
        n_lengths = len(lengths)
        candidate_periods = []
        for k in range(1, len(spec)):
            if spec[k] <= 0:
                continue
            period = n_lengths / k
            if 4.0 <= period <= 30.0:
                candidate_periods.append((spec[k], period))
        if candidate_periods:
            candidate_periods.sort(reverse=True)
            _top_strength, top_period = candidate_periods[0]
            # Bias toward metric (10) since all our sample rulers carry a metric scale.
            # An imperial-period of 16 is only chosen when the FFT clearly favours it.
            if 6.0 <= top_period <= 13.0:
                minors_per_major = 10
            elif 13.0 < top_period <= 24.0:
                minors_per_major = 16
            elif 3.0 <= top_period < 6.0:
                minors_per_major = 5
            else:
                minors_per_major = max(1, int(round(top_period)))

    # Build major_indices: every Nth tick anchored at the longest one we find.
    if minors_per_major > 1 and len(lengths) >= minors_per_major:
        # For each possible phase, sum the lengths at every `minors_per_major`-th peak
        # and pick the phase whose sum is largest (that's where the major ticks sit).
        best_phase = 0
        best_sum = -1.0
        for phase in range(minors_per_major):
            s = float(lengths[phase::minors_per_major].sum())
            if s > best_sum:
                best_sum = s
                best_phase = phase
        major_indices = np.arange(best_phase, len(peaks), minors_per_major)
    else:
        # Fall back to Otsu on lengths if FFT didn't yield a confident period.
        if lengths.max() == lengths.min():
            major_indices = np.arange(len(peaks))
        else:
            lengths_u8 = (255 * (lengths - lengths.min()) / (lengths.max() - lengths.min() + 1e-6)).astype(np.uint8)
            _, length_thr = cv2.threshold(lengths_u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            major_indices = np.where(length_thr.flatten() > 0)[0]
        if len(major_indices) >= 2:
            major_gaps = np.diff(major_indices)
            minors_per_major = int(round(float(np.median(major_gaps))))
        else:
            minors_per_major = max(1, len(peaks) // max(1, len(major_indices) or 1))

    # Bonus to quality when the hierarchy matches a known ruler subdivision.
    if minors_per_major in (8, 10, 16):
        hierarchy_bonus = 2.0
    elif minors_per_major in (5, 20):
        hierarchy_bonus = 1.2
    else:
        hierarchy_bonus = 1.0
    quality *= hierarchy_bonus

    return {
        "quality": float(quality),
        "peaks": peaks,
        "diffs": diffs,
        "lengths": lengths,
        "dx_minor": dx_minor,
        "minors_per_major": int(minors_per_major),
        "major_indices": major_indices,
    }


def calibrate_scale(ruler_roi: RulerROI) -> ScaleInfo:
    """Run tick-pattern calibration on both axes and pick the more confident result."""
    warped = ruler_roi.warped_gray
    _, b_inv = cv2.threshold(warped, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    results = []
    for axis in (0, 1):
        r = _calibrate_along_axis(b_inv, axis)
        if r is not None:
            results.append((axis, r))

    if not results:
        raise RuntimeError("Could not find regular tick peaks on ruler in either orientation")

    tick_axis, r = max(results, key=lambda ar: ar[1]["quality"])
    peaks = r["peaks"]
    lengths = r["lengths"]
    major_indices = r["major_indices"]
    dx_minor = r["dx_minor"]
    minors_per_major = r["minors_per_major"]

    unit, mm_per_minor = _decide_unit(minors_per_major)

    pixels_per_mm = dx_minor / mm_per_minor

    if len(major_indices) >= 2:
        first_p = peaks[major_indices[0]]
        last_p = peaks[major_indices[-1]]
    else:
        first_p = peaks[0]
        last_p = peaks[-1]

    if tick_axis == 0:
        mid_y = warped.shape[0] // 2
        seg_warped = ((int(first_p), int(mid_y)), (int(last_p), int(mid_y)))
    else:
        mid_x = warped.shape[1] // 2
        seg_warped = ((int(mid_x), int(first_p)), (int(mid_x), int(last_p)))

    out_w = warped.shape[1]
    out_h = warped.shape[0]
    dst = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype=np.float32,
    )
    M = cv2.getPerspectiveTransform(ruler_roi.quad, dst)
    M_inv = np.linalg.inv(M)
    seg_arr = np.array([[seg_warped[0]], [seg_warped[1]]], dtype=np.float32)
    seg_orig = cv2.perspectiveTransform(seg_arr, M_inv).reshape(2, 2)

    calib_px = float(np.linalg.norm(np.array(seg_warped[1]) - np.array(seg_warped[0])))
    calib_mm = calib_px / pixels_per_mm

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


# ----------------------------------------------------------------------------
# Stage 3: Tray mask
# ----------------------------------------------------------------------------
def find_tray_mask(bgr_img: np.ndarray, ruler_quad: np.ndarray) -> np.ndarray:
    h, w = bgr_img.shape[:2]
    hsv = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    s = hsv[:, :, 1]
    bright = ((v > 150) & (s < 80)).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
    closed = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, kernel)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    if n <= 1:
        mask = np.ones((h, w), dtype=np.uint8) * 255
    else:
        sizes = stats[1:, cv2.CC_STAT_AREA]
        largest = 1 + int(np.argmax(sizes))
        mask = (labels == largest).astype(np.uint8) * 255
        if mask.sum() / 255 < 0.05 * h * w:
            mask = np.ones((h, w), dtype=np.uint8) * 255
    mask = cv2.erode(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)))
    ruler_mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(ruler_mask, [ruler_quad.astype(np.int32)], 255)
    ruler_mask = cv2.dilate(ruler_mask, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
    mask[ruler_mask > 0] = 0
    return mask


# ----------------------------------------------------------------------------
# Stage 4: Eye blob detection
# ----------------------------------------------------------------------------
def detect_eye_blobs(bgr_img: np.ndarray, mask: np.ndarray, scale: ScaleInfo):
    gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    head_px = max(7, int(round(3.0 * scale.pixels_per_mm)))
    if head_px % 2 == 0:
        head_px += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (head_px, head_px))
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)
    blackhat = cv2.bitwise_and(blackhat, blackhat, mask=mask)

    vals = blackhat[mask > 0]
    if vals.size == 0:
        return []
    thr_val = max(15, int(np.percentile(vals, 99.0) * 0.4))
    binimg = (blackhat > thr_val).astype(np.uint8) * 255

    n, _labels, stats, centroids = cv2.connectedComponentsWithStats(binimg, connectivity=8)

    min_eye_mm2 = (0.05) ** 2 * np.pi
    max_eye_mm2 = (0.4) ** 2 * np.pi
    min_eye_px = max(2, int(min_eye_mm2 * scale.pixels_per_mm ** 2))
    max_eye_px = max(min_eye_px + 4, int(max_eye_mm2 * scale.pixels_per_mm ** 2))

    eyes = []
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_eye_px or area > max_eye_px:
            continue
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        if max(bw, bh) > 3 * min(bw, bh):
            continue
        cx, cy = centroids[i]
        eyes.append((float(cx), float(cy), area))
    return eyes


# ----------------------------------------------------------------------------
# Stage 5: Eye pairing
# ----------------------------------------------------------------------------
def pair_eyes(eyes, scale: ScaleInfo, bgr_img: np.ndarray):
    if len(eyes) < 2:
        return []
    pts = np.array([(e[0], e[1]) for e in eyes], dtype=np.float32)
    min_mm = 0.4
    max_mm = 2.5
    min_px = min_mm * scale.pixels_per_mm
    max_px = max_mm * scale.pixels_per_mm

    n = len(pts)
    candidates = []
    for i in range(n):
        for j in range(i + 1, n):
            d = float(np.linalg.norm(pts[i] - pts[j]))
            if min_px <= d <= max_px:
                candidates.append((d, i, j))
    candidates.sort()

    used = set()
    measurements = []
    gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY) if bgr_img is not None else None

    larva_id = 1
    for d, i, j in candidates:
        if i in used or j in used:
            continue
        p1 = pts[i]
        p2 = pts[j]
        keep = True
        if gray is not None:
            h, w = gray.shape
            mx = int((p1[0] + p2[0]) / 2)
            my = int((p1[1] + p2[1]) / 2)
            if 5 <= mx < w - 5 and 5 <= my < h - 5:
                mid_patch = gray[my - 3: my + 4, mx - 3: mx + 4]
                ring_r = int(max(8, d * 1.5))
                y0 = max(0, my - ring_r); y1 = min(h, my + ring_r + 1)
                x0 = max(0, mx - ring_r); x1 = min(w, mx + ring_r + 1)
                bg_patch = gray[y0:y1, x0:x1]
                if mid_patch.mean() > bg_patch.mean() + 5:
                    keep = False
        if not keep:
            continue
        used.add(i)
        used.add(j)
        distance_mm = d / scale.pixels_per_mm
        measurements.append(
            Measurement(
                larva_id=larva_id,
                eye1=(float(p1[0]), float(p1[1])),
                eye2=(float(p2[0]), float(p2[1])),
                distance_px=d,
                distance_mm=distance_mm,
                distance_cm=distance_mm / 10.0,
            )
        )
        larva_id += 1
    return measurements
