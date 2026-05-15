import math
import pytest
from crab_measurebot_2000.app import (
    distance_point_to_segment,
    compute_distance_mm,
    images_in_dir,
    screen_to_image,
    image_to_screen,
)


def test_distance_on_segment():
    assert distance_point_to_segment(5, 0, 0, 0, 10, 0) == pytest.approx(0.0)


def test_distance_perpendicular():
    assert distance_point_to_segment(5, 3, 0, 0, 10, 0) == pytest.approx(3.0)


def test_distance_past_endpoint():
    assert distance_point_to_segment(15, 0, 0, 0, 10, 0) == pytest.approx(5.0)


def test_compute_distance_mm_basic():
    # scale: 100px = 10mm → measurement 50px = 5mm
    assert compute_distance_mm(0, 0, 50, 0, 0, 0, 100, 0, 10.0) == pytest.approx(5.0)


def test_compute_distance_mm_diagonal():
    # scale: sqrt(2)*100 px = 10mm → measurement sqrt(2)*50 px = 5mm
    result = compute_distance_mm(0, 0, math.sqrt(2) * 50, 0, 0, 0, 100, 100, 10.0)
    assert result == pytest.approx(5.0)


def test_compute_distance_mm_zero_scale_raises():
    with pytest.raises(ValueError):
        compute_distance_mm(0, 0, 50, 0, 5, 5, 5, 5, 10.0)


def test_screen_to_image_identity():
    assert screen_to_image(100.0, 50.0, 1.0, 0.0, 0.0) == (
        pytest.approx(100.0),
        pytest.approx(50.0),
    )


def test_screen_to_image_zoom_pan():
    # zoom=2, pan=(20,10): screen(120,60) → image(50,25)
    assert screen_to_image(120.0, 60.0, 2.0, 20.0, 10.0) == (
        pytest.approx(50.0),
        pytest.approx(25.0),
    )


def test_image_to_screen_roundtrip():
    ix, iy = 75.0, 30.0
    sx, sy = image_to_screen(ix, iy, 1.5, 10.0, 5.0)
    rx, ry = screen_to_image(sx, sy, 1.5, 10.0, 5.0)
    assert (rx, ry) == (pytest.approx(ix), pytest.approx(iy))


def test_images_in_dir(tmp_path):
    (tmp_path / "a.jpg").touch()
    (tmp_path / "b.JPEG").touch()
    (tmp_path / "c.png").touch()
    (tmp_path / "d.txt").touch()
    result = images_in_dir(tmp_path)
    assert [p.name for p in result] == ["a.jpg", "b.JPEG", "c.png"]
