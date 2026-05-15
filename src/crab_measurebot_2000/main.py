from __future__ import annotations
import math
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


def distance_point_to_segment(
    px: float, py: float,
    x1: float, y1: float, x2: float, y2: float,
) -> float:
    dx, dy = x2 - x1, y2 - y1
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / seg_len_sq))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def compute_distance_mm(
    m_x1: float, m_y1: float, m_x2: float, m_y2: float,
    s_x1: float, s_y1: float, s_x2: float, s_y2: float,
    scale_mm: float,
) -> float:
    scale_px = math.hypot(s_x2 - s_x1, s_y2 - s_y1)
    if scale_px == 0.0:
        raise ValueError("Scale segment has zero length")
    meas_px = math.hypot(m_x2 - m_x1, m_y2 - m_y1)
    return meas_px * scale_mm / scale_px


def screen_to_image(sx: float, sy: float, zoom: float, pan_x: float, pan_y: float) -> tuple[float, float]:
    return (sx - pan_x) / zoom, (sy - pan_y) / zoom


def image_to_screen(ix: float, iy: float, zoom: float, pan_x: float, pan_y: float) -> tuple[float, float]:
    return ix * zoom + pan_x, iy * zoom + pan_y


def images_in_dir(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)


from tortoise import fields
from tortoise.models import Model
import csv as _csv


class Image(Model):
    id = fields.IntField(primary_key=True)
    path = fields.CharField(max_length=1024, unique=True)
    scale_x1 = fields.FloatField(null=True)
    scale_y1 = fields.FloatField(null=True)
    scale_x2 = fields.FloatField(null=True)
    scale_y2 = fields.FloatField(null=True)
    scale_mm = fields.FloatField(null=True)  # stored as mm; user enters cm × 10

    class Meta:
        table = "images"


class Measurement(Model):
    id = fields.IntField(primary_key=True)
    image = fields.ForeignKeyField("models.Image", related_name="measurements")
    x1 = fields.FloatField()
    y1 = fields.FloatField()
    x2 = fields.FloatField()
    y2 = fields.FloatField()
    distance_mm = fields.FloatField()

    class Meta:
        table = "measurements"


async def export_csv(output_path: Path) -> None:
    measurements = await Measurement.all().select_related("image").order_by("image__path", "id")
    with output_path.open("w", newline="") as f:
        writer = _csv.DictWriter(
            f,
            fieldnames=["image_path", "measurement_id", "x1", "y1", "x2", "y2", "distance_mm"],
        )
        writer.writeheader()
        for m in measurements:
            writer.writerow({
                "image_path": m.image.path,
                "measurement_id": m.id,
                "x1": m.x1, "y1": m.y1, "x2": m.x2, "y2": m.y2,
                "distance_mm": round(m.distance_mm, 4),
            })
