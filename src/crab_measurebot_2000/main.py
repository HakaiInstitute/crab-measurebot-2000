from __future__ import annotations
import asyncio
import csv as _csv
import dataclasses
import math
from pathlib import Path
from typing import Literal

import cv2
import qasync
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QEvent
from PySide6.QtGui import (
    QPainter, QPixmap, QImage, QColor, QPen, QFont,
)
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QMainWindow, QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)
from tortoise import fields
from tortoise.models import Model

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


# ---------------------------------------------------------------------------
# Task 5: AppState dataclass
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class AppState:
    image_index: int = 0
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0
    pending_point: tuple[float, float] | None = None  # first click, image coords
    mode: Literal["idle", "placing_iod", "placing_scale", "panning"] = "idle"
    drag_start_screen: tuple[float, float] | None = None
    drag_start_pan: tuple[float, float] | None = None
    image_record: Image | None = None
    measurements: list[Measurement] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# Task 6 & 7: ImageCanvas — rendering + mouse interaction
# ---------------------------------------------------------------------------

class ImageCanvas(QWidget):
    scale_segment_placed = Signal(float, float, float, float)
    iod_segment_placed = Signal(float, float, float, float)
    scale_deleted = Signal()
    measurement_deleted = Signal(int)

    HIT_THRESHOLD_PX = 6

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self._state = state
        self._pixmap: QPixmap | None = None
        self._rubber_end: tuple[float, float] | None = None
        self._pre_pan_mode: Literal["idle", "placing_iod"] = "idle"
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def load_image(self, path: Path) -> None:
        bgr = cv2.imread(str(path))
        if bgr is None:
            self._pixmap = None
            self.update()
            return
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        self._pixmap = QPixmap.fromImage(qimg)
        self._fit_to_window()
        self._rubber_end = None
        self.update()

    def _fit_to_window(self) -> None:
        if not self._pixmap:
            return
        pw, ph = self._pixmap.width(), self._pixmap.height()
        ww, wh = self.width() or 1, self.height() or 1
        self._state.zoom = max(0.001, min(ww / pw, wh / ph))
        self._state.pan_x = (ww - pw * self._state.zoom) / 2
        self._state.pan_y = (wh - ph * self._state.zoom) / 2

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._pixmap:
            self._fit_to_window()
        self.update()

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self._zoom_at(event.position().x(), event.position().y(), factor)
        event.accept()

    def event(self, ev):
        if ev.type() == QEvent.Type.NativeGesture:
            from PySide6.QtGui import QNativeGestureEvent
            if isinstance(ev, QNativeGestureEvent):
                if ev.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                    self._zoom_at(ev.localPos().x(), ev.localPos().y(), 1.0 + ev.value())
                    ev.accept()
                    return True
        return super().event(ev)

    def _zoom_at(self, cx: float, cy: float, factor: float) -> None:
        new_zoom = max(0.05, min(50.0, self._state.zoom * factor))
        ratio = new_zoom / self._state.zoom
        self._state.pan_x = cx - (cx - self._state.pan_x) * ratio
        self._state.pan_y = cy - (cy - self._state.pan_y) * ratio
        self._state.zoom = new_zoom
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0d0d1a"))
        if self._pixmap is None:
            painter.setPen(QColor("#444"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No image")
            painter.end()
            return
        z, px, py = self._state.zoom, self._state.pan_x, self._state.pan_y
        pw, ph = self._pixmap.width(), self._pixmap.height()
        painter.drawPixmap(QRectF(px, py, pw * z, ph * z), self._pixmap, QRectF(self._pixmap.rect()))
        self._draw_overlays(painter)
        painter.end()

    def _draw_overlays(self, painter: QPainter) -> None:
        ir = self._state.image_record
        if ir and ir.scale_x1 is not None:
            self._draw_segment(painter, ir.scale_x1, ir.scale_y1, ir.scale_x2, ir.scale_y2,
                               QColor("#ffdd00"), f"{ir.scale_mm:.1f} mm")
        for i, m in enumerate(self._state.measurements):
            self._draw_segment(painter, m.x1, m.y1, m.x2, m.y2,
                               QColor("#00eeff"), f"#{i+1} · {m.distance_mm:.2f} mm")
        if self._state.pending_point and self._rubber_end:
            p1, p2 = self._state.pending_point, self._rubber_end
            sx1, sy1 = image_to_screen(p1[0], p1[1], self._state.zoom, self._state.pan_x, self._state.pan_y)
            sx2, sy2 = image_to_screen(p2[0], p2[1], self._state.zoom, self._state.pan_x, self._state.pan_y)
            c = QColor("#ffdd00" if self._state.mode == "placing_scale" else "#00eeff")
            c.setAlphaF(0.5)
            pen = QPen(c, 2, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(sx1, sy1), QPointF(sx2, sy2))
            painter.setBrush(c)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(sx1, sy1), 5, 5)
        if self._state.mode != "idle":
            mode_text = {"placing_iod": "● MEASURE MODE", "placing_scale": "● SCALE MODE",
                         "panning": "✥ PANNING"}.get(self._state.mode, "")
            if mode_text:
                painter.setPen(QColor("#00c864"))
                painter.setFont(QFont("monospace", 9))
                painter.drawText(QPointF(10, self.height() - 10), mode_text)
        painter.setPen(QColor("#555"))
        painter.setFont(QFont("monospace", 9))
        painter.drawText(QPointF(10, 16), f"zoom: {self._state.zoom:.2f}×")

    def _draw_segment(self, painter: QPainter, x1: float, y1: float, x2: float, y2: float,
                      color: QColor, label: str) -> None:
        z, px, py = self._state.zoom, self._state.pan_x, self._state.pan_y
        sx1, sy1 = image_to_screen(x1, y1, z, px, py)
        sx2, sy2 = image_to_screen(x2, y2, z, px, py)
        painter.setPen(QPen(color, 2))
        painter.drawLine(QPointF(sx1, sy1), QPointF(sx2, sy2))
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(sx1, sy1), 5, 5)
        painter.drawEllipse(QPointF(sx2, sy2), 5, 5)
        mx, my = (sx1 + sx2) / 2, (sy1 + sy2) / 2 - 8
        painter.setFont(QFont("monospace", 9))
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(label)
        painter.fillRect(QRectF(mx - tw / 2 - 3, my - fm.ascent(), tw + 6, fm.height()),
                         QColor(0, 0, 0, 160))
        painter.setPen(color)
        painter.drawText(QPointF(mx - tw / 2, my), label)

    def mousePressEvent(self, event):
        sx, sy = event.position().x(), event.position().y()
        ix, iy = screen_to_image(sx, sy, self._state.zoom, self._state.pan_x, self._state.pan_y)

        if event.button() == Qt.MouseButton.LeftButton:
            if self._state.mode == "placing_scale":
                self._state.pending_point = None
                self._state.mode = "idle"
                self._rubber_end = None
                self.update()
                return
            self._state.drag_start_screen = (sx, sy)
            self._state.drag_start_pan = (self._state.pan_x, self._state.pan_y)

        elif event.button() == Qt.MouseButton.RightButton:
            if self._state.mode == "placing_iod":
                self._state.pending_point = None
                self._state.mode = "idle"
                self._rubber_end = None
                self.update()
                return
            if self._state.mode == "idle":
                self._state.mode = "placing_scale"
                self._state.pending_point = (ix, iy)
                self._rubber_end = (ix, iy)
            elif self._state.mode == "placing_scale":
                p1 = self._state.pending_point
                if p1 is None:
                    return
                self._state.pending_point = None
                self._state.mode = "idle"
                self._rubber_end = None
                self.scale_segment_placed.emit(p1[0], p1[1], ix, iy)
            self.update()

    def mouseMoveEvent(self, event):
        sx, sy = event.position().x(), event.position().y()
        ix, iy = screen_to_image(sx, sy, self._state.zoom, self._state.pan_x, self._state.pan_y)

        if (self._state.mode in ("idle", "placing_iod") and self._state.drag_start_screen
                and event.buttons() & Qt.MouseButton.LeftButton):
            dx = sx - self._state.drag_start_screen[0]
            dy = sy - self._state.drag_start_screen[1]
            if math.hypot(dx, dy) >= 5:
                self._pre_pan_mode = "placing_iod" if self._state.mode == "placing_iod" else "idle"
                self._state.mode = "panning"
                self.setCursor(Qt.CursorShape.ClosedHandCursor)

        if self._state.mode == "panning":
            if self._state.drag_start_pan is None or self._state.drag_start_screen is None:
                self._state.mode = self._pre_pan_mode
                self._pre_pan_mode = "idle"
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self._state.pan_x = self._state.drag_start_pan[0] + (sx - self._state.drag_start_screen[0])
                self._state.pan_y = self._state.drag_start_pan[1] + (sy - self._state.drag_start_screen[1])
                self.update()
        elif self._state.mode in ("placing_iod", "placing_scale"):
            self._rubber_end = (ix, iy)
            self.update()

    def mouseReleaseEvent(self, event):
        sx, sy = event.position().x(), event.position().y()
        ix, iy = screen_to_image(sx, sy, self._state.zoom, self._state.pan_x, self._state.pan_y)

        if event.button() == Qt.MouseButton.LeftButton:
            if self._state.mode == "panning":
                self._state.mode = self._pre_pan_mode
                self._pre_pan_mode = "idle"
                self.setCursor(Qt.CursorShape.CrossCursor)
            elif self._state.mode == "idle" and self._state.drag_start_screen is not None:
                if not self._try_delete_at(ix, iy):
                    self._state.mode = "placing_iod"
                    self._state.pending_point = (ix, iy)
                    self._rubber_end = (ix, iy)
            elif self._state.mode == "placing_iod":
                p1 = self._state.pending_point
                if p1 is None:
                    return
                self._state.pending_point = None
                self._state.mode = "idle"
                self._rubber_end = None
                self.iod_segment_placed.emit(p1[0], p1[1], ix, iy)
            self._state.drag_start_screen = None
            self._state.drag_start_pan = None
            self.update()

    def _try_delete_at(self, ix: float, iy: float) -> bool:
        threshold = self.HIT_THRESHOLD_PX / self._state.zoom
        ir = self._state.image_record
        if ir and ir.scale_x1 is not None:
            if distance_point_to_segment(ix, iy, ir.scale_x1, ir.scale_y1,
                                         ir.scale_x2, ir.scale_y2) <= threshold:
                self.scale_deleted.emit()
                return True
        for m in reversed(self._state.measurements):
            if distance_point_to_segment(ix, iy, m.x1, m.y1, m.x2, m.y2) <= threshold:
                self.measurement_deleted.emit(m.id)
                return True
        return False


# ---------------------------------------------------------------------------
# Task 8: RightPanel widget
# ---------------------------------------------------------------------------

class RightPanel(QWidget):
    export_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(200)
        self.setStyleSheet("background:#111;color:#ccc;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._file_label = QLabel("—")
        self._file_label.setStyleSheet("color:#fff;font-weight:bold;font-size:11px;")
        self._file_label.setWordWrap(True)
        self._counter_label = QLabel("")
        self._counter_label.setStyleSheet("color:#666;font-size:10px;")
        self._progress_bar = QProgressBar()
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(3)
        self._progress_bar.setStyleSheet(
            "QProgressBar{background:#333;border-radius:1px;}"
            "QProgressBar::chunk{background:#4466aa;border-radius:1px;}"
        )
        layout.addWidget(self._section([self._file_label, self._counter_label, self._progress_bar]))

        self._scale_status = QLabel("Not set")
        self._scale_status.setStyleSheet("color:#666;font-size:10px;")
        layout.addWidget(self._section([
            self._header("SCALE CALIBRATION"),
            self._scale_status,
            self._hint("Right-click × 2 to set"),
        ]))

        self._meas_layout = QVBoxLayout()
        self._meas_layout.setSpacing(4)
        self._meas_layout.setContentsMargins(0, 0, 0, 0)
        meas_container = QWidget()
        meas_container.setStyleSheet("background:transparent;")
        meas_container.setLayout(self._meas_layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border:none;background:transparent;")
        scroll.setMaximumHeight(200)
        scroll.setWidget(meas_container)
        layout.addWidget(self._section([
            self._header("MEASUREMENTS"),
            scroll,
            self._hint("Left-click × 2 to add · click line to delete"),
        ]))

        controls = [("← →","navigate"),("scroll","zoom"),("L-drag","pan"),
                    ("L-click","IOD point"),("R-click","scale point"),("E","export CSV"),("Del","delete last")]
        ctrl_rows = []
        for key, desc in controls:
            row = QHBoxLayout()
            k = QLabel(key); k.setStyleSheet("color:#888;font-size:9px;min-width:40px;")
            d = QLabel(desc); d.setStyleSheet("color:#555;font-size:9px;")
            row.addWidget(k); row.addWidget(d); row.addStretch()
            ctrl_rows.append(row)
        ctrl_frame = QFrame()
        ctrl_frame.setStyleSheet("border-bottom:1px solid #222;")
        cl = QVBoxLayout(ctrl_frame)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.addWidget(self._header("CONTROLS"))
        for r in ctrl_rows:
            cl.addLayout(r)
        layout.addWidget(ctrl_frame)

        layout.addStretch()

        self._export_btn = QPushButton("Export CSV (E)")
        self._export_btn.setStyleSheet(
            "background:#1a2a1a;border:1px solid #2a4a2a;color:#00c864;"
            "font-size:10px;padding:6px;border-radius:4px;margin:10px;")
        self._export_btn.clicked.connect(self.export_requested)
        layout.addWidget(self._export_btn)

    def _section(self, widgets: list) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet("border-bottom:1px solid #222;")
        vl = QVBoxLayout(frame)
        vl.setContentsMargins(12, 10, 12, 10)
        for w in widgets:
            if isinstance(w, QWidget):
                vl.addWidget(w)
            else:
                vl.addLayout(w)
        return frame

    def _header(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#aaa;font-size:9px;letter-spacing:0.5px;margin-bottom:4px;")
        return lbl

    def _hint(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#444;font-size:9px;")
        return lbl

    def update_file_info(self, filename: str, index: int, total: int) -> None:
        self._file_label.setText(filename)
        self._counter_label.setText(f"Image {index + 1} of {total}")
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(index + 1)

    def flash_scale_warning(self) -> None:
        self._scale_status.setText("⚠ Set scale first!")
        self._scale_status.setStyleSheet("color:#ff6644;font-size:10px;font-weight:bold;")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2000, lambda: self.update_scale(None))

    def update_scale(self, image_record) -> None:
        if image_record and image_record.scale_mm is not None:
            scale_px = math.hypot(image_record.scale_x2 - image_record.scale_x1,
                                  image_record.scale_y2 - image_record.scale_y1)
            self._scale_status.setText(f"✓ {image_record.scale_mm:.1f} mm\n{scale_px/image_record.scale_mm:.1f} px/mm")
            self._scale_status.setStyleSheet("color:#00c864;font-size:10px;")
        else:
            self._scale_status.setText("Not set")
            self._scale_status.setStyleSheet("color:#666;font-size:10px;")

    def update_measurements(self, measurements: list) -> None:
        while self._meas_layout.count():
            item = self._meas_layout.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.deleteLater()
        for i, m in enumerate(measurements):
            lbl = QLabel(f"#{i+1}  {m.distance_mm:.2f} mm")
            lbl.setStyleSheet("background:#0d1f0d;border:1px solid #1a3a1a;"
                              "border-radius:4px;padding:4px 6px;color:#00eeff;font-size:10px;")
            self._meas_layout.addWidget(lbl)


# ---------------------------------------------------------------------------
# Task 9: MainWindow
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, image_dir: Path):
        super().__init__()
        self.setWindowTitle("CrabMeasureBot 2000")
        self.setMinimumSize(900, 600)
        self._image_dir = image_dir
        self._images = images_in_dir(image_dir)
        self._state = AppState()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = ImageCanvas(self._state)
        self._panel = RightPanel()
        layout.addWidget(self._canvas, stretch=1)
        layout.addWidget(self._panel)

        self._canvas.scale_segment_placed.connect(self._on_scale_placed)
        self._canvas.iod_segment_placed.connect(self._on_iod_placed)
        self._canvas.scale_deleted.connect(self._on_scale_deleted)
        self._canvas.measurement_deleted.connect(self._on_measurement_deleted)
        self._panel.export_requested.connect(lambda: asyncio.ensure_future(self._on_export()))

        self._load_task: asyncio.Task | None = None
        if self._images:
            self._load_task = asyncio.ensure_future(self._load_image(0))

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Right:
            self._navigate(1)
        elif key == Qt.Key.Key_Left:
            self._navigate(-1)
        elif key == Qt.Key.Key_E:
            asyncio.ensure_future(self._on_export())
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._state.measurements:
                asyncio.ensure_future(self._do_delete_measurement(self._state.measurements[-1].id))
        else:
            super().keyPressEvent(event)

    def _navigate(self, delta: int) -> None:
        if not self._images:
            return
        if self._load_task and not self._load_task.done():
            self._load_task.cancel()
        self._state.image_index = (self._state.image_index + delta) % len(self._images)
        self._load_task = asyncio.ensure_future(self._load_image(self._state.image_index))

    async def _load_image(self, index: int) -> None:
        path = self._images[index]
        img_record, _ = await Image.get_or_create(path=str(path))
        self._state.image_record = img_record
        self._state.measurements = list(await img_record.measurements.all().order_by("id"))
        self._canvas.load_image(path)
        self._panel.update_file_info(path.name, index, len(self._images))
        self._panel.update_scale(img_record)
        self._panel.update_measurements(self._state.measurements)

    @qasync.asyncSlot(float, float, float, float)
    async def _on_scale_placed(self, x1: float, y1: float, x2: float, y2: float) -> None:
        value_cm, ok = QInputDialog.getDouble(
            self, "Scale Calibration", "Length of this segment (cm):", 1.0, 0.001, 10000.0, 3
        )
        if not ok:
            return
        ir = self._state.image_record
        if ir is None:
            return
        ir.scale_x1, ir.scale_y1 = x1, y1
        ir.scale_x2, ir.scale_y2 = x2, y2
        ir.scale_mm = value_cm * 10.0
        await ir.save()
        self._panel.update_scale(ir)
        self._canvas.update()

    @qasync.asyncSlot(float, float, float, float)
    async def _on_iod_placed(self, x1: float, y1: float, x2: float, y2: float) -> None:
        ir = self._state.image_record
        if ir is None or ir.scale_mm is None:
            self._panel.flash_scale_warning()
            return
        dist_mm = compute_distance_mm(x1, y1, x2, y2,
                                      ir.scale_x1, ir.scale_y1, ir.scale_x2, ir.scale_y2,
                                      ir.scale_mm)
        m = await Measurement.create(image=ir, x1=x1, y1=y1, x2=x2, y2=y2, distance_mm=dist_mm)
        self._state.measurements.append(m)
        self._panel.update_measurements(self._state.measurements)
        self._canvas.update()

    @qasync.asyncSlot()
    async def _on_scale_deleted(self) -> None:
        ir = self._state.image_record
        if ir is None:
            return
        ir.scale_x1 = ir.scale_y1 = ir.scale_x2 = ir.scale_y2 = ir.scale_mm = None
        await ir.save()
        self._panel.update_scale(ir)
        self._canvas.update()

    @qasync.asyncSlot(int)
    async def _on_measurement_deleted(self, measurement_id: int) -> None:
        await self._do_delete_measurement(measurement_id)

    async def _do_delete_measurement(self, measurement_id: int) -> None:
        m = await Measurement.get_or_none(id=measurement_id)
        if m:
            await m.delete()
        self._state.measurements = [x for x in self._state.measurements if x.id != measurement_id]
        self._panel.update_measurements(self._state.measurements)
        self._canvas.update()

    async def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", str(self._image_dir / "measurements.csv"), "CSV Files (*.csv)"
        )
        if path:
            await export_csv(Path(path))
