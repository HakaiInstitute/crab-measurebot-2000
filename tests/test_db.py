import pytest
from pathlib import Path

from crab_measurebot_2000.app import Image, Measurement


@pytest.mark.asyncio
async def test_create_image():
    img = await Image.create(path="/tmp/test.jpg")
    assert img.id is not None
    assert img.scale_x1 is None


@pytest.mark.asyncio
async def test_set_scale():
    img = await Image.create(path="/tmp/test.jpg")
    img.scale_x1, img.scale_y1 = 10.0, 20.0
    img.scale_x2, img.scale_y2 = 110.0, 20.0
    img.scale_mm = 10.0
    await img.save()
    reloaded = await Image.get(id=img.id)
    assert reloaded.scale_mm == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_create_measurement():
    img = await Image.create(
        path="/tmp/test.jpg",
        scale_x1=0,
        scale_y1=0,
        scale_x2=100,
        scale_y2=0,
        scale_mm=10.0,
    )
    m = await Measurement.create(image=img, x1=10, y1=50, x2=60, y2=50, distance_mm=5.0)
    assert m.id is not None


@pytest.mark.asyncio
async def test_measurements_for_image():
    img = await Image.create(path="/tmp/test.jpg")
    await Measurement.create(image=img, x1=0, y1=0, x2=10, y2=0, distance_mm=1.0)
    await Measurement.create(image=img, x1=0, y1=0, x2=20, y2=0, distance_mm=2.0)
    assert len(await img.measurements.all()) == 2


@pytest.mark.asyncio
async def test_delete_measurement():
    img = await Image.create(path="/tmp/test.jpg")
    m = await Measurement.create(image=img, x1=0, y1=0, x2=10, y2=0, distance_mm=1.0)
    await m.delete()
    assert len(await img.measurements.all()) == 0


@pytest.mark.asyncio
async def test_image_path_is_stored_relative(tmp_path):
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "photo.jpg"

    rel_path = str(image_path.relative_to(image_dir))
    img, created = await Image.get_or_create(path=rel_path)
    assert created
    assert img.path == "photo.jpg"
    assert not Path(img.path).is_absolute()

    # Same relative path lookup finds the same record
    img2, created2 = await Image.get_or_create(path=rel_path)
    assert not created2
    assert img2.id == img.id
