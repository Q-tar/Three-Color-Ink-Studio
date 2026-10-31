"""Ink Studio desktop host for the Board2 ESP32-C3 e-paper display."""
import argparse
from dataclasses import asdict, fields
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from PIL import Image, ImageDraw
from PySide6.QtCore import Qt, QTimer, Signal, QSettings, QProcess, QRectF
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor, QFont, QPen, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSlider, QCheckBox, QFileDialog, QMessageBox, QPlainTextEdit,
    QFrame, QProgressBar, QScrollArea, QSizePolicy, QSplitter, QLineEdit,
)
from serial.tools import list_ports

from image_core import Options, convert, load_image, export_header
from protocol import image_command
from device import SerialWorker
from wifi_device import WifiWorker

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
RESOURCE = Path(getattr(sys, "_MEIPASS", ROOT))


STYLE = """
QMainWindow, QWidget#root { background: #f3f5f7; color: #172638; }
QWidget { font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif; font-size: 13px; color: #172638; }
QFrame#header, QFrame#card, QFrame#controls { background: white; border: 1px solid #dfe5eb; border-radius: 12px; }
QLabel#brand { font-size: 25px; font-weight: 700; color: #152b3d; }
QLabel#title { font-size: 15px; font-weight: 700; }
QLabel#muted { color: #64768a; font-size: 12px; }
QLabel#badge { color: #176658; background: #e8f5ef; border-radius: 10px; padding: 7px 12px; font-weight: 600; }
QPushButton { background: white; border: 1px solid #cfd9e2; border-radius: 7px; padding: 8px 12px; }
QPushButton:hover { background: #edf3f7; border-color: #91a8b9; }
QPushButton:pressed { background: #dce8ee; }
QPushButton:disabled { color: #9ca9b5; background: #f3f5f7; border-color: #e3e8ed; }
QPushButton#primary { background: #176c5d; border-color: #176c5d; color: white; font-weight: 700; }
QPushButton#primary:hover { background: #105448; }
QPushButton#primary:disabled { background: #abc6bf; border-color: #abc6bf; color: #f8fcfa; }
QComboBox { background: white; border: 1px solid #cfd9e2; border-radius: 6px; padding: 7px; min-height: 18px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #deeee8; }
QScrollArea { border: none; background: transparent; }
QSlider::groove:horizontal { height: 5px; background: #dce5eb; border-radius: 2px; }
QSlider::handle:horizontal { background: #176c5d; width: 15px; margin: -5px 0; border-radius: 7px; }
QCheckBox { spacing: 7px; padding: 4px 0; }
QProgressBar { border: none; background: #e3e9ee; border-radius: 4px; min-height: 8px; max-height: 8px; }
QProgressBar::chunk { background: #176c5d; border-radius: 4px; }
QPlainTextEdit { background: #142535; color: #d3e2ed; border: none; border-radius: 8px; padding: 8px; font-family: Consolas, 'Microsoft YaHei UI'; font-size: 12px; }
QSplitter::handle { background: transparent; height: 8px; }
"""


def label(text, name=None, wrap=False):
    obj = QLabel(text)
    if name:
        obj.setObjectName(name)
    obj.setWordWrap(wrap)
    return obj


def button(text, slot, primary=False):
    obj = QPushButton(text)
    if primary:
        obj.setObjectName("primary")
    obj.clicked.connect(slot)
    return obj


def card():
    frame = QFrame()
    frame.setObjectName("card")
    return frame


class ImageCanvas(QWidget):
    moved = Signal(float, float)
    zoomed = Signal(int)

    def __init__(self, editable=False):
        super().__init__()
        self.editable = editable
        self.pixmap = None
        self.rect = QRectF()
        self.drag_position = None
        self.setMinimumSize(160, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        if editable:
            self.setCursor(Qt.OpenHandCursor)
            self.setToolTip("拖动调整图片位置；滚轮调整缩放")

    def set_image(self, image):
        rgb = image.convert("RGB")
        data = rgb.tobytes()
        # Copy into Qt-owned storage; the Python byte buffer ends with this call.
        qt_image = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format_RGB888).copy()
        self.pixmap = QPixmap.fromImage(qt_image)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.contentsRect(), QColor("#eef2f5"))
        if self.pixmap is None:
            p.setPen(QColor("#81909d"))
            p.drawText(self.contentsRect(), Qt.AlignCenter, "导入图片开始设计")
            return
        scale = min((self.width()-36) / self.pixmap.width(), (self.height()-30) / self.pixmap.height())
        w, h = self.pixmap.width()*scale, self.pixmap.height()*scale
        self.rect = QRectF((self.width()-w)/2, (self.height()-h)/2, w, h)
        p.fillRect(self.rect.adjusted(-4, -4, 4, 4), QColor("#253746"))
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.drawPixmap(self.rect, self.pixmap, QRectF(self.pixmap.rect()))

    def mousePressEvent(self, event):
        if self.editable and event.button() == Qt.LeftButton and self.rect.contains(event.position()):
            self.drag_position = event.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self.drag_position is not None:
            delta = event.position() - self.drag_position
            self.drag_position = event.position()
            self.moved.emit(delta.x()/max(1, self.rect.width()), delta.y()/max(1, self.rect.height()))

    def mouseReleaseEvent(self, event):
        self.drag_position = None
        if self.editable:
            self.setCursor(Qt.OpenHandCursor)

    def wheelEvent(self, event):
        if self.editable:
            self.zoomed.emit(5 if event.angleDelta().y() > 0 else -5)
            event.accept()


class MainWindow(QMainWindow):
    def __init__(self, simulate=False, image_path=None, settings=None):
        super().__init__()
        self.setWindowTitle("墨迹 Ink Studio · 墨水屏图片工作台")
        self.resize(1280, 880)
        self.setMinimumSize(1000, 720)
        self.setAcceptDrops(True)
        self.settings = settings if settings is not None else QSettings("InkStudio", "E2213JS0C")
        self.options = Options()
        try:
            saved = json.loads(self.settings.value("options", "{}"))
            self.options = Options(**{k: v for k, v in saved.items() if k in {f.name for f in fields(Options)}})
        except (ValueError, TypeError):
            pass
        self.source = None
        self.result = None
        self.worker = None
        self.ready = False
        self.device_busy = False
        self.saved_image = False
        self.storage_available = True
        self.request_id = 100
        self.active_id = None
        self.active_crc = None
        self.job_started = 0
        self.handshake_started = 0
        self.last_hello = 0
        self.process = None
        self.process_kind = ""
        self.process_buffer = ""
        self.sliders = {}
        self.recompute = QTimer(self)
        self.recompute.setSingleShot(True)
        self.recompute.timeout.connect(self.update_preview)
        self.build_ui()
        self.ports.currentIndexChanged.connect(lambda _: self.update_buttons())
        self.refresh_ports()
        if simulate:
            self.ports.setCurrentIndex(self.ports.findData("SIMULATOR"))
        if image_path:
            self.open_image(image_path)
        elif (RESOURCE / "assets" / "example.jpg").exists():
            self.open_image(RESOURCE / "assets" / "example.jpg")
        else:
            self.use_pattern()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(500)
        self.log("就绪。首次使用请烧录配套接收固件；现有显示固件不能直接接收图片。")

    def build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(14)
        header = card()
        header.setObjectName("header")
        bar = QHBoxLayout(header)
        bar.setContentsMargins(18, 13, 18, 13)
        title = QVBoxLayout()
        title.addWidget(label("墨迹  /  Ink Studio", "brand"))
        title.addWidget(label("把一张图片，留在墨水屏上。", "muted"))
        bar.addLayout(title)
        bar.addStretch()
        bar.addWidget(label("2.13 英寸  ·  黑 / 白 / 红", "badge"))
        self.transport = QComboBox()
        self.transport.addItems(["USB", "Wi-Fi"])
        self.transport.currentIndexChanged.connect(self.update_buttons)
        bar.addWidget(self.transport)
        self.wifi_address = QLineEdit(str(self.settings.value("wifi_address", "192.168.4.1")))
        self.wifi_address.setMaximumWidth(185)
        self.wifi_address.setPlaceholderText("192.168.4.1")
        self.wifi_address.setToolTip("先连接热点 InkStudio-C3，密码 inkstudio123；此处填写设备 IP。")
        bar.addWidget(self.wifi_address)
        self.ports = QComboBox()
        self.ports.setMinimumWidth(205)
        bar.addWidget(self.ports)
        self.scan_btn = button("扫描", self.refresh_ports)
        bar.addWidget(self.scan_btn)
        self.connect_btn = button("连接设备", self.toggle_connection, True)
        bar.addWidget(self.connect_btn)
        layout.addWidget(header)
        self.wifi_hint = label("先连接电脑 Wi-Fi：InkStudio-C3  ·  密码：inkstudio123  ·  地址：192.168.4.1  ·  提示无 Internet 时保持连接；设备仍需供电。", "muted", True)
        layout.addWidget(self.wifi_hint)

        splitter = QSplitter(Qt.Vertical)
        upper = QWidget()
        body = QHBoxLayout(upper)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(14)
        controls = card()
        controls.setObjectName("controls")
        controls.setFixedWidth(270)
        settings_layout = QVBoxLayout(controls)
        settings_layout.setContentsMargins(16, 16, 16, 16)
        settings_layout.addWidget(label("01   图片与转换", "title"))
        files = QHBoxLayout()
        files.addWidget(button("导入图片", self.choose_image, True))
        files.addWidget(button("测试图", self.use_pattern))
        settings_layout.addLayout(files)
        self.file_label = label("支持 JPG / PNG / BMP / WEBP", "muted", True)
        settings_layout.addWidget(self.file_label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        settings_content = QWidget()
        sl = QVBoxLayout(settings_content)
        sl.setContentsMargins(0, 7, 2, 4)
        sl.setSpacing(9)
        self.orientation = QComboBox()
        self.orientation.addItems(["竖屏  104 × 212", "横屏  212 × 104"])
        self.orientation.setCurrentIndex(int(self.options.landscape))
        self.orientation.currentIndexChanged.connect(self.change_layout)
        sl.addWidget(self.orientation)
        self.fit = QComboBox()
        self.fit.addItems(["完整显示 · 保留白边", "铺满画布 · 裁剪边缘"])
        self.fit.setCurrentIndex(int(self.options.fill))
        self.fit.currentIndexChanged.connect(self.change_layout)
        sl.addWidget(self.fit)
        rotates = QHBoxLayout()
        rotates.addWidget(button("↶  左转", lambda: self.rotate(90)))
        rotates.addWidget(button("右转  ↷", lambda: self.rotate(-90)))
        sl.addLayout(rotates)
        self.add_slider(sl, "zoom", "缩放", 100, 400, round(self.options.zoom*100), lambda v: f"{v}%")
        self.add_slider(sl, "threshold", "黑色阈值", 30, 240, self.options.threshold, str)
        self.add_slider(sl, "red_gap", "红色识别差值", 15, 100, self.options.red_gap, str)
        self.add_slider(sl, "brightness", "亮度", 50, 180, round(self.options.brightness*100), lambda v: f"{v}%")
        self.add_slider(sl, "contrast", "对比度", 50, 200, round(self.options.contrast*100), lambda v: f"{v}%")
        self.dither = QCheckBox("黑白抖动 · 适合照片")
        self.dither.setChecked(self.options.dither)
        self.dither.toggled.connect(self.controls_changed)
        sl.addWidget(self.dither)
        self.red_check = QCheckBox("启用红色色层")
        self.red_check.setChecked(self.options.use_red)
        self.red_check.toggled.connect(self.controls_changed)
        sl.addWidget(self.red_check)
        sl.addWidget(button("重置转换参数", self.reset_options))
        sl.addWidget(label("拖动左侧画布调整位置，滚轮缩放。\n预览为像素模拟，实际颜色以屏幕为准。", "muted", True))
        sl.addStretch()
        scroll.setWidget(settings_content)
        settings_layout.addWidget(scroll, 1)
        body.addWidget(controls)

        workspace = card()
        wl = QVBoxLayout(workspace)
        wl.setContentsMargins(18, 16, 18, 16)
        row = QHBoxLayout()
        row.addWidget(label("02   构图与屏幕预览", "title"))
        row.addStretch()
        self.dim_label = label("104 × 212", "muted")
        row.addWidget(self.dim_label)
        wl.addLayout(row)
        canvases = QHBoxLayout()
        for text, editable in (("构图 · 可拖动", True), ("黑白红 · 最终效果", False)):
            col = QVBoxLayout()
            col.addWidget(label(text, "muted"), alignment=Qt.AlignCenter)
            canvas = ImageCanvas(editable)
            col.addWidget(canvas, 1)
            canvases.addLayout(col, 1)
            if editable:
                self.colour_canvas = canvas
                canvas.moved.connect(self.pan)
                canvas.zoomed.connect(lambda d: self.sliders["zoom"].setValue(self.sliders["zoom"].value()+d))
            else:
                self.preview_canvas = canvas
        wl.addLayout(canvases, 1)
        self.image_stats = label("", "muted")
        wl.addWidget(self.image_stats)
        row = QHBoxLayout()
        self.export_btn = button("导出 PNG / C 数组 / BIN", self.export)
        row.addWidget(self.export_btn)
        row.addStretch()
        self.save_check = QCheckBox("同时保存到设备")
        self.save_check.setToolTip("刷新流程完成后写入 Flash；下次可重绘保存的图片。默认不写 Flash。")
        row.addWidget(self.save_check)
        self.send_btn = button("发送到墨水屏", self.send_image, True)
        row.addWidget(self.send_btn)
        wl.addLayout(row)
        body.addWidget(workspace, 1)
        splitter.addWidget(upper)

        bottom = card()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(16, 12, 16, 12)
        row = QHBoxLayout()
        self.status_label = label("设备未连接 · 可先处理和导出图片", "title")
        row.addWidget(self.status_label, 1)
        self.restore_btn = button("重绘已存图片", self.restore_image)
        row.addWidget(self.restore_btn)
        self.flash_btn = button("首次安装接收固件", self.flash_firmware)
        row.addWidget(self.flash_btn)
        row.addWidget(button("打开使用说明", self.open_help))
        bl.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        bl.addWidget(self.progress)
        self.stage_label = label("全刷模式：25℃典型约 22 秒；本程序暂不启用自动休眠。", "muted")
        bl.addWidget(self.stage_label)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(1200)
        bl.addWidget(self.logs, 1)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([610, 200])
        layout.addWidget(splitter, 1)
        self.footer = label("E2213JS0C1/C2  ·  Board2  ·  ESP-IDF 5.4  |  BUSY 3 / RST 1 / DC 5 / CLK 4 / MOSI 6 / CS 7", "muted")
        layout.addWidget(self.footer)
        self.update_buttons()

    def add_slider(self, layout, key, title, minimum, maximum, value, formatter):
        row = QHBoxLayout()
        row.addWidget(label(title))
        value_label = label(formatter(value), "muted")
        row.addStretch()
        row.addWidget(value_label)
        layout.addLayout(row)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        slider.valueChanged.connect(lambda v: value_label.setText(formatter(v)))
        slider.valueChanged.connect(self.controls_changed)
        self.sliders[key] = slider
        layout.addWidget(slider)

    def log(self, message):
        self.logs.appendPlainText(datetime.now().strftime("%H:%M:%S") + "  " + message)

    def controls_changed(self, *_):
        for key, slider in self.sliders.items():
            value = slider.value()
            setattr(self.options, key, value/100 if key in ("zoom", "brightness", "contrast") else value)
        self.options.dither = self.dither.isChecked()
        self.options.use_red = self.red_check.isChecked()
        self.recompute.start(40)

    def change_layout(self, *_):
        self.options.landscape = bool(self.orientation.currentIndex())
        self.options.fill = bool(self.fit.currentIndex())
        self.options.pan_x = self.options.pan_y = 0
        self.recompute.start(40)

    def rotate(self, angle):
        self.options.rotation = (self.options.rotation+angle) % 360
        self.options.pan_x = self.options.pan_y = 0
        self.update_preview()

    def pan(self, dx, dy):
        self.options.pan_x = max(-4, min(4, self.options.pan_x+dx))
        self.options.pan_y = max(-4, min(4, self.options.pan_y+dy))
        self.recompute.start(15)

    def reset_options(self):
        defaults = Options()
        controls = [self.orientation, self.fit, self.dither, self.red_check, *self.sliders.values()]
        for control in controls:
            control.blockSignals(True)
        self.orientation.setCurrentIndex(0)
        self.fit.setCurrentIndex(0)
        for key, slider in self.sliders.items():
            value = getattr(defaults, key)
            slider.setValue(round(value*100) if key in ("zoom", "brightness", "contrast") else value)
        self.dither.setChecked(False)
        self.red_check.setChecked(True)
        self.options = defaults
        for control in controls:
            control.blockSignals(False)
        for slider in self.sliders.values():
            slider.valueChanged.emit(slider.value())
        self.recompute.stop()
        self.update_preview()

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入图片", str(self.settings.value("last_dir", ROOT)),
                                              "图片 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*)")
        if path:
            self.open_image(path)

    def open_image(self, path):
        try:
            image = load_image(path)
            # Composition is at most 212x104: cap working copy to keep large photos responsive.
            image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
            self.source = image
            self.options.pan_x = self.options.pan_y = 0
            self.file_label.setText(Path(path).name + f"\n{image.width} × {image.height}")
            self.settings.setValue("last_dir", str(Path(path).parent))
            self.update_preview()
        except Exception as exc:
            QMessageBox.warning(self, "无法打开图片", str(exc))

    def use_pattern(self):
        img = Image.new("RGB", (212, 212), "white")
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((12, 12, 200, 200), radius=20, outline="black", width=5)
        draw.rectangle((32, 38, 180, 82), fill=(210, 38, 48))
        for i in range(6):
            draw.line((32, 105+i*12, 180-i*14, 105+i*12), fill="black", width=4)
        self.source = img
        self.file_label.setText("内置测试图\n黑 / 白 / 红色块与线条")
        self.options.pan_x = self.options.pan_y = 0
        self.update_preview()

    def update_preview(self):
        if self.source is None:
            return
        self.result = convert(self.source, self.options)
        self.colour_canvas.set_image(self.result.colour)
        self.preview_canvas.set_image(self.result.preview)
        w, h = self.result.preview.size
        self.dim_label.setText(f"{w} × {h}  ·  原生数据 104 × 212")
        black_count = sum(v.bit_count() for v in self.result.black)
        red_count = sum(v.bit_count() for v in self.result.red)
        self.image_stats.setText(f"黑色 {black_count} px   /   红色 {red_count} px   /   5512 字节   /   CRC {self.result.crc.upper()}")
        self.update_buttons()

    def export(self):
        if self.result is None:
            return
        path, kind = QFileDialog.getSaveFileName(self, "导出屏幕图像", str(ROOT / "exports" / "epd_preview.png"),
                                                "预览 PNG (*.png);;C 数组 (*.h);;双层原始数据 (*.bin)")
        if not path:
            return
        try:
            target = Path(path)
            suffix = ".h" if "*.h" in kind else ".bin" if "*.bin" in kind else ".png"
            if target.suffix.lower() != suffix:
                target = target.with_suffix(suffix)
            target.parent.mkdir(parents=True, exist_ok=True)
            if suffix == ".h":
                export_header(self.result, target)
            elif suffix == ".bin":
                target.write_bytes(self.result.payload)
            else:
                self.result.preview.save(target)
            self.log("已导出：" + str(target))
            self.stage_label.setText("导出成功：" + target.name)
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def refresh_ports(self):
        current = self.ports.currentData()
        self.ports.clear()
        for port in sorted(list_ports.comports(), key=lambda p: p.device):
            self.ports.addItem(f"{port.device}  ·  {port.description}", port.device)
        self.ports.addItem("模拟设备 · 不连接硬件", "SIMULATOR")
        selected = self.ports.findData(current or "COM9")
        self.ports.setCurrentIndex(max(0, selected))

    def toggle_connection(self):
        if self.worker:
            self.disconnect_device()
            return
        self.ready = False
        self.device_busy = False
        wifi = self.transport.currentIndex() == 1
        self.worker = WifiWorker(self.wifi_address.text(), self) if wifi else SerialWorker(self.ports.currentData(), self)
        self.worker.opened.connect(self.on_opened)
        self.worker.received.connect(self.on_event)
        self.worker.log.connect(self.log)
        self.worker.failed.connect(self.on_serial_error)
        self.worker.transferred.connect(self.on_transferred)
        self.worker.start()
        self.status_label.setText("正在连接 Wi-Fi 设备…" if wifi else "正在打开串口…")
        self.update_buttons()

    def on_opened(self):
        self.handshake_started = time.monotonic()
        self.last_hello = 0
        name = "Wi-Fi 已连接" if self.transport.currentIndex() == 1 else "串口已打开"
        self.status_label.setText(name + " · 等待接收固件握手")
        self.log(name + "，正在识别设备。")

    def disconnect_device(self):
        if self.worker:
            self.worker.requestInterruption()
            if not self.worker.wait(6500):
                self.log("正在等待连接关闭，请稍后重试。")
                return False
            self.worker.deleteLater()
            self.worker = None
        if self.active_id is not None:
            self.log("连接已断开，设备可能仍在刷新；断开连接不会取消屏幕刷新。")
        self.active_id = None
        self.ready = False
        self.device_busy = False
        self.handshake_started = 0
        self.status_label.setText("设备未连接 · 可先处理和导出图片")
        self.update_buttons()
        return True

    def on_serial_error(self, text):
        wifi = self.transport.currentIndex() == 1
        self.log("连接错误：" + text)
        self.disconnect_device()
        self.status_label.setText("连接失败 · 网络连接已关闭" if wifi else "连接失败 · 端口已释放")
        self.stage_label.setText(text.split("\n", 1)[0])

    def next_id(self):
        self.request_id += 1
        return self.request_id

    def on_transferred(self, percent):
        if self.active_id is not None and not self.device_busy:
            self.progress.setValue(round(percent*.15))

    def on_event(self, event):
        kind = event.get("event")
        rid = event.get("id")
        if kind == "hello":
            if event.get("protocol") != 1 or (event.get("width"), event.get("height")) != (104, 212):
                self.ready = False
                self.status_label.setText("设备协议或分辨率不兼容")
                self.update_buttons()
                return
            first = not self.ready
            self.ready = True
            self.handshake_started = 0
            self.device_busy = bool(event.get("busy"))
            self.saved_image = bool(event.get("saved"))
            self.storage_available = event.get("storage_available", True)
            if not self.storage_available:
                self.save_check.setChecked(False)
            self.save_check.setEnabled(self.storage_available)
            simulated = event.get("simulated")
            name = "模拟设备" if simulated else "墨水屏"
            if self.active_id is None:
                self.status_label.setText(name + (" · 正在刷新" if self.device_busy else " · 已连接"))
            if first:
                self.log(f"握手成功：{event.get('firmware')} / 104 × 212" + ("（模拟，不会操作硬件）" if simulated else ""))
                if not self.storage_available:
                    message = "设备存储不可用，仍可传图显示，但暂不能保存：" + str(event.get("storage_error", "未知错误"))
                    self.log(message)
                    self.stage_label.setText(message)
            self.update_buttons()
            return
        if kind == "ready":
            if self.active_id is not None:
                self.log("设备重新启动，上一项刷新结果未知。")
                self.active_id = None
                self.active_crc = None
            self.ready = False
            self.handshake_started = time.monotonic()
            self.last_hello = 0
            self.update_buttons()
            return
        if self.active_id is None or rid != self.active_id:
            if kind == "error":
                self.log("设备报告：" + str(event.get("message", event)))
            return
        if kind == "accepted":
            if self.active_crc is not None and event.get("crc") != self.active_crc:
                self.fail_job("设备接收 CRC 与发送内容不一致")
                return
            self.active_crc = event.get("crc")
            self.device_busy = True
            self.progress.setValue(20)
            self.status_label.setText("数据已接收 · 正在处理")
            self.log(f"设备已校验图片：{event.get('bytes')} 字节，CRC {self.active_crc}")
        elif kind == "stage":
            stages = {"reset": 25, "upload": 35, "power_on": 45, "refresh": 60, "power_off": 90, "save": 95}
            self.progress.setValue(stages.get(event.get("stage"), self.progress.value()))
            self.stage_label.setText(str(event.get("message", "处理中")))
            self.log(self.stage_label.text())
        elif kind == "done":
            if self.active_crc is None or event.get("crc") != self.active_crc:
                self.fail_job("完成回复 CRC 不匹配，不能确认本次操作")
                return
            self.progress.setValue(100)
            self.status_label.setText("模拟流程完成" if event.get("simulated") else "刷新流程完成 · 请查看屏幕")
            self.stage_label.setText(f"耗时 {event.get('elapsed_ms', 0)/1000:.1f} 秒" + (" · 已保存到设备" if event.get("saved") else " · 未写入 Flash"))
            self.log(self.status_label.text() + "；" + self.stage_label.text())
            self.active_id = None
            self.device_busy = False
            if event.get("saved"):
                self.saved_image = True
        elif kind == "error":
            self.fail_job(str(event.get("message", "设备处理失败")))
        self.update_buttons()

    def fail_job(self, message):
        self.log("操作未完成：" + message)
        self.status_label.setText("操作未完成")
        self.stage_label.setText(message)
        self.active_id = None
        self.device_busy = False
        self.ready = False  # Require a fresh status reply before accepting another image.
        self.handshake_started = time.monotonic()
        self.last_hello = 0
        self.update_buttons()

    def send_image(self):
        if not self.ready or self.device_busy or self.active_id is not None or self.result is None:
            return
        # Recompute synchronously if controls have a debounced update pending.
        self.recompute.stop()
        self.update_preview()
        self.active_id = self.next_id()
        self.active_crc = self.result.crc
        self.job_started = time.monotonic()
        self.progress.setValue(0)
        self.status_label.setText("正在传输图片…")
        self.stage_label.setText("等待设备校验长度与 CRC；刷新期间请保持供电。")
        self.worker.send(image_command(self.active_id, self.result.payload, self.save_check.isChecked()))
        self.log(f"发送图片，CRC {self.active_crc}，保存={self.save_check.isChecked()}")
        self.update_buttons()

    def restore_image(self):
        if not self.ready or self.device_busy or self.active_id is not None:
            return
        self.active_id = self.next_id()
        self.active_crc = None
        self.job_started = time.monotonic()
        self.progress.setValue(0)
        self.status_label.setText("正在重绘已保存图片…")
        self.worker.send({"id": self.active_id, "cmd": "restore"})
        self.update_buttons()

    def tick(self):
        now = time.monotonic()
        if self.worker and self.worker.isRunning() and self.handshake_started:
            if now-self.handshake_started > 10:
                wifi = self.transport.currentIndex() == 1
                self.log("握手超时：未收到兼容的接收固件回复。请检查连接及接收固件。")
                self.disconnect_device()
                self.status_label.setText("Wi-Fi 握手超时 · 连接已关闭" if wifi else "未识别到接收固件 · 端口已释放")
                self.stage_label.setText("请连接 InkStudio-C3 热点并核对设备地址；首次安装固件请切回 USB。" if wifi else "请先安装配套接收固件；已安装则按 RESET 或重新插拔 C3 的 USB 后连接。")
                return
            if now-self.last_hello >= 1.5:
                self.worker.send({"id": self.next_id(), "cmd": "hello"})
                self.last_hello = now
        if self.active_id is not None and now-self.job_started > 400:
            self.fail_job("等待设备回复超时；结果未知，请查看日志并重新查询设备。")
        if self.worker and self.ready and self.active_id is None and self.device_busy and now-self.last_hello > 2:
            self.worker.send({"id": self.next_id(), "cmd": "status"})
            self.last_hello = now

    def update_buttons(self):
        flashing = self.process is not None
        connected = self.worker is not None
        working = self.active_id is not None or self.device_busy
        wifi = self.transport.currentIndex() == 1
        self.transport.setEnabled(not connected and not flashing)
        self.wifi_address.setVisible(wifi)
        self.wifi_hint.setVisible(wifi)
        self.wifi_address.setEnabled(not connected and not flashing)
        self.ports.setVisible(not wifi)
        self.scan_btn.setVisible(not wifi)
        self.connect_btn.setText("断开连接" if connected else "连接设备")
        self.connect_btn.setEnabled(not flashing)
        self.ports.setEnabled(not connected and not flashing)
        self.scan_btn.setEnabled(not connected and not flashing)
        self.send_btn.setEnabled(self.ready and not working and not flashing and self.result is not None)
        self.restore_btn.setEnabled(self.ready and not working and not flashing and self.saved_image)
        self.flash_btn.setEnabled(not wifi and not working and not flashing and self.ports.currentData() != "SIMULATOR")
        self.flash_btn.setToolTip("首次安装或更新固件请切换 USB 模式并连接数据线。")
        self.export_btn.setEnabled(self.result is not None)

    def flash_firmware(self):
        port = self.ports.currentData()
        if not port or port == "SIMULATOR":
            return
        answer = QMessageBox.question(self, "安装接收固件", f"将向 {port} 烧录配套的 Board2 接收固件。\n"
                                      "这会替换板上现有程序，桌面原工程文件不会修改。\n\n继续安装？")
        if answer != QMessageBox.Yes:
            return
        if not self.disconnect_device():
            return
        script = ROOT / "firmware" / "flash_firmware.ps1"
        if not script.exists():
            QMessageBox.warning(self, "文件缺失", "找不到配套烧录脚本，请使用完整文件夹。")
            return
        self.process = QProcess(self)
        self.process.setProgram("powershell.exe")
        self.process.setArguments(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Port", port])
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_process)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.process_error)
        self.status_label.setText("正在安装接收固件…")
        self.stage_label.setText("请保持 USB 连接；烧录成功后点击连接设备。")
        self.progress.setRange(0, 0)
        self.update_buttons()
        self.process.start()

    def read_process(self):
        if self.process:
            chunk = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
            self.process_buffer += chunk.replace("\r", "\n")
            parts = self.process_buffer.split("\n")
            self.process_buffer = parts.pop()
            for line in parts:
                if line.strip():
                    self.log(line)

    def process_error(self, error):
        if error == QProcess.FailedToStart:
            self.log("无法启动烧录程序：" + self.process.errorString())
            self.process_finished(-1)

    def process_finished(self, code, *_):
        if self.process is None:
            return
        self.read_process()
        if self.process_buffer.strip():
            self.log(self.process_buffer.strip())
        self.process_buffer = ""
        self.process.deleteLater()
        self.process = None
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if code == 0 else 0)
        self.status_label.setText("固件安装成功 · 请连接设备" if code == 0 else "固件安装失败 · 查看日志")
        self.stage_label.setText("若端口号变化，点击扫描并选择新的端口。")
        self.update_buttons()

    def open_help(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ROOT / "使用说明.md")))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self.open_image(urls[0].toLocalFile())

    def closeEvent(self, event):
        if self.process is not None:
            QMessageBox.information(self, "正在烧录", "请等待烧录结束后再关闭程序。")
            event.ignore()
            return
        if self.active_id is not None or self.device_busy:
            answer = QMessageBox.question(self, "设备仍在刷新", "关闭窗口不会取消屏幕刷新，请保持设备供电。\n确定关闭？")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        if not self.disconnect_device():
            event.ignore()
            return
        self.settings.setValue("options", json.dumps(asdict(self.options)))
        self.settings.setValue("wifi_address", self.wifi_address.text().strip())
        self.settings.sync()
        event.accept()


def packaged_self_check(qt, report_path, wifi_address=None):
    """Exercise the distributed runtime without opening a window or a real port."""
    checks = []
    window = None
    result = {}
    try:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "check.ini"), QSettings.IniFormat)
            window = MainWindow(simulate=True, settings=settings)
            assert window.result is not None and not window.preview_canvas.pixmap.isNull()
            checks.append("bundled_image_and_QImage_preview")

            def until(predicate):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    qt.processEvents()
                    if predicate():
                        return
                    time.sleep(.01)
                raise RuntimeError(window.logs.toPlainText())

            window.toggle_connection()
            until(lambda: window.ready)
            checks.append("threaded_simulator_handshake")
            window.save_check.setChecked(True)
            window.send_image()
            until(lambda: window.active_id is None)
            assert window.saved_image and window.progress.value() == 100
            assert "模拟流程完成" in window.status_label.text()
            checks.append("upload_crc_refresh_save")
            window.restore_image()
            until(lambda: window.active_id is None)
            assert "模拟流程完成" in window.status_label.text()
            checks.append("restore_saved_image")
            assert window.disconnect_device()
            checks.append("disconnect")
            if wifi_address:
                window.transport.setCurrentIndex(1)
                window.wifi_address.setText(wifi_address)
                window.toggle_connection()
                until(lambda: window.ready)
                checks.append("HTTP_wifi_handshake")
                window.send_image()
                until(lambda: window.active_id is None)
                assert window.progress.value() == 100 and window.saved_image
                checks.append("HTTP_wifi_upload_save")
                window.restore_image()
                until(lambda: window.active_id is None)
                assert window.progress.value() == 100
                checks.append("HTTP_wifi_restore")
                assert window.disconnect_device()
            result = {"ok": True, "checks": checks, "frozen": bool(getattr(sys, "frozen", False))}
    except Exception as exc:
        result = {"ok": False, "checks": checks, "error": repr(exc)}
    finally:
        if window is not None:
            window.timer.stop()
            window.recompute.stop()
            window.disconnect_device()
        Path(report_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result.get("ok") else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--image")
    parser.add_argument("--self-check", metavar="REPORT_JSON")
    parser.add_argument("--self-check-wifi-address", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_check:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(STYLE)
    if args.self_check:
        sys.exit(packaged_self_check(app, args.self_check, args.self_check_wifi_address))
    window = MainWindow(args.simulate, args.image)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
