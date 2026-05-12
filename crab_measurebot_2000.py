# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "marimo",
#     "opencv-python>=4.10",
#     "numpy>=2.0",
#     "matplotlib>=3.9",
# ]
# ///

import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import cv2
    import numpy as np
    import matplotlib.pyplot as plt
    from pathlib import Path
    import crab_pipeline as cp

    return Path, cp, cv2, mo, np, plt


@app.cell
def _(mo):
    is_script_mode = mo.app_meta().mode == "script"
    return (is_script_mode,)


@app.cell
def _(Path, mo):
    image_paths = sorted(Path("data").glob("*.JPEG"))
    image_picker = mo.ui.dropdown(
        options={p.name: str(p) for p in image_paths},
        value=image_paths[0].name if image_paths else None,
        label="Image",
    )
    image_picker
    return image_paths, image_picker


@app.cell
def _(cv2, image_paths, image_picker, is_script_mode):
    if is_script_mode:
        selected_path = str(image_paths[0])
    else:
        selected_path = image_picker.value or str(image_paths[0])

    bgr = cv2.imread(selected_path)
    if bgr is None:
        raise FileNotFoundError(f"Could not read image: {selected_path}")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return bgr, rgb, selected_path


@app.cell
def _(plt, rgb, selected_path):
    fig_input, ax_input = plt.subplots(figsize=(8, 6))
    ax_input.imshow(rgb)
    ax_input.set_title(f"Input: {selected_path}")
    ax_input.axis("off")
    fig_input
    return


@app.cell
def _(bgr, cp, cv2, np, plt, rgb):
    ruler_roi = cp.detect_ruler(bgr)

    fig_ruler, ax_ruler = plt.subplots(1, 2, figsize=(12, 5))
    overlay = rgb.copy()
    _quad_int = ruler_roi.quad.astype(np.int32).reshape(-1, 1, 2)
    cv2.polylines(overlay, [_quad_int], isClosed=True, color=(255, 0, 0), thickness=4)
    ax_ruler[0].imshow(overlay)
    ax_ruler[0].set_title(f"Detected ruler ROI (score={ruler_roi.score:.1f})")
    ax_ruler[0].axis("off")
    ax_ruler[1].imshow(cv2.cvtColor(ruler_roi.warped_bgr, cv2.COLOR_BGR2RGB))
    ax_ruler[1].set_title("Rectified ruler patch")
    ax_ruler[1].axis("off")
    fig_ruler
    return (ruler_roi,)


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


@app.cell
def _(bgr, cp, plt, rgb, ruler_roi):
    tray_mask = cp.find_tray_mask(bgr, ruler_roi.quad)

    fig_tray, ax_tray = plt.subplots(figsize=(8, 6))
    overlay_tray = rgb.copy()
    overlay_tray[tray_mask == 0] = (overlay_tray[tray_mask == 0] * 0.3).astype("uint8")
    ax_tray.imshow(overlay_tray)
    ax_tray.set_title("Tray mask (bright = valid search region)")
    ax_tray.axis("off")
    fig_tray
    return (tray_mask,)


@app.cell
def _(bgr, cp, plt, rgb, scale, tray_mask):
    eyes = cp.detect_eye_blobs(bgr, tray_mask, scale)

    fig_eyes, ax_eyes = plt.subplots(figsize=(8, 6))
    ax_eyes.imshow(rgb)
    for ex, ey, _a in eyes:
        ax_eyes.plot(ex, ey, "o", markersize=8, markerfacecolor="none", markeredgecolor="red", markeredgewidth=1.5)
    ax_eyes.set_title(f"Eye blob candidates: {len(eyes)}")
    ax_eyes.axis("off")
    fig_eyes
    return (eyes,)


@app.cell
def _(bgr, cp, eyes, scale):
    measurements = cp.pair_eyes(eyes, scale, bgr)
    return (measurements,)


@app.cell
def _(cv2, measurements, np, plt, rgb, ruler_roi, scale):
    annotated = rgb.copy()
    _quad_int = ruler_roi.quad.astype(np.int32).reshape(-1, 1, 2)
    cv2.polylines(annotated, [_quad_int], isClosed=True, color=(255, 0, 0), thickness=3)
    (sx1, sy1), (sx2, sy2) = scale.calib_segment_orig
    cv2.line(annotated, (int(sx1), int(sy1)), (int(sx2), int(sy2)), (255, 255, 0), 4)
    cv2.circle(annotated, (int(sx1), int(sy1)), 6, (255, 255, 0), -1)
    cv2.circle(annotated, (int(sx2), int(sy2)), 6, (255, 255, 0), -1)
    label = f"{scale.calib_mm:.1f} mm  ({scale.pixels_per_mm:.1f} px/mm, {scale.unit})"
    label_pos = (int((sx1 + sx2) / 2) + 8, int((sy1 + sy2) / 2) - 8)
    cv2.putText(annotated, label, label_pos, cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)

    for m in measurements:
        e1 = (int(m.eye1[0]), int(m.eye1[1]))
        e2 = (int(m.eye2[0]), int(m.eye2[1]))
        cv2.circle(annotated, e1, 5, (0, 255, 0), 2)
        cv2.circle(annotated, e2, 5, (0, 255, 0), 2)
        cv2.line(annotated, e1, e2, (0, 255, 0), 2)
        mid = (int((e1[0] + e2[0]) / 2) + 6, int((e1[1] + e2[1]) / 2) - 6)
        cv2.putText(annotated, f"#{m.larva_id} {m.distance_cm:.2f} cm", mid,
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

    fig_final, ax_final = plt.subplots(figsize=(12, 9))
    ax_final.imshow(annotated)
    ax_final.set_title(f"Annotated: {len(measurements)} larvae measured")
    ax_final.axis("off")
    fig_final
    return


@app.cell
def _(measurements, mo):
    rows = [
        {
            "larva_id": m.larva_id,
            "eye1_x": round(m.eye1[0], 1),
            "eye1_y": round(m.eye1[1], 1),
            "eye2_x": round(m.eye2[0], 1),
            "eye2_y": round(m.eye2[1], 1),
            "distance_px": round(m.distance_px, 2),
            "distance_mm": round(m.distance_mm, 3),
            "distance_cm": round(m.distance_cm, 3),
        }
        for m in measurements
    ]
    table = mo.ui.table(rows, label=f"{len(rows)} larvae")
    table
    return


if __name__ == "__main__":
    app.run()
