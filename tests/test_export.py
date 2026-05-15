import csv
import pytest
from pathlib import Path
from crab_measurebot_2000.main import Image, Measurement, export_csv


@pytest.mark.asyncio
async def test_export_columns(tmp_path):
    img = await Image.create(path="/data/photo.jpg")
    await Measurement.create(image=img, x1=0, y1=0, x2=30, y2=40, distance_mm=3.5)
    out = tmp_path / "out.csv"
    await export_csv(out)
    reader = csv.DictReader(out.open())
    assert reader.fieldnames == ["image_path", "measurement_id", "x1", "y1", "x2", "y2", "distance_mm"]


@pytest.mark.asyncio
async def test_export_values(tmp_path):
    img = await Image.create(path="/data/photo.jpg", scale_x1=0, scale_y1=0,
                             scale_x2=100, scale_y2=0, scale_mm=10.0)
    await Measurement.create(image=img, x1=10, y1=20, x2=60, y2=20, distance_mm=5.0)
    out = tmp_path / "out.csv"
    await export_csv(out)
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 1
    assert rows[0]["image_path"] == "/data/photo.jpg"
    assert float(rows[0]["distance_mm"]) == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_export_multiple_images(tmp_path):
    img1 = await Image.create(path="/data/a.jpg")
    img2 = await Image.create(path="/data/b.jpg")
    await Measurement.create(image=img1, x1=0, y1=0, x2=10, y2=0, distance_mm=1.0)
    await Measurement.create(image=img2, x1=0, y1=0, x2=20, y2=0, distance_mm=2.0)
    out = tmp_path / "out.csv"
    await export_csv(out)
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 2
    assert {r["image_path"] for r in rows} == {"/data/a.jpg", "/data/b.jpg"}
