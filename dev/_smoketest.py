"""Run the pipeline against every sample JPEG and print a summary."""
from pathlib import Path

import cv2

import crab_pipeline as cp


def main():
    for path in sorted(Path("data").glob("*.JPEG")):
        bgr = cv2.imread(str(path))
        if bgr is None:
            print(f"{path.name}: could not read")
            continue
        print(f"\n=== {path.name} ===")
        try:
            ruler = cp.detect_ruler(bgr)
            print(f"  ruler: score={ruler.score:.1f}, warped={ruler.warped_gray.shape}")
            scale = cp.calibrate_scale(ruler)
            print(
                f"  scale: tick_axis={'x' if scale.tick_axis == 0 else 'y'}, "
                f"{scale.pixels_per_mm:.2f} px/mm, {scale.unit}, "
                f"minors/major={scale.minors_per_major}, calib={scale.calib_mm:.1f}mm"
            )
            tray = cp.find_tray_mask(bgr, ruler.quad)
            print(f"  tray coverage: {(tray > 0).mean() * 100:.1f}%")
            eyes = cp.detect_eye_blobs(bgr, tray, scale)
            print(f"  eye blob candidates: {len(eyes)}")
            measurements = cp.pair_eyes(eyes, scale, bgr)
            print(f"  larvae paired: {len(measurements)}")
            if measurements:
                ds = sorted(m.distance_mm for m in measurements)
                print(
                    f"    interocular: min={ds[0]:.2f} median={ds[len(ds) // 2]:.2f} "
                    f"max={ds[-1]:.2f} mm"
                )
        except Exception as e:
            print(f"  FAIL: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
