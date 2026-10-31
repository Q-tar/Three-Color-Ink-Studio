"""Serial I/O and local simulator; all blocking work stays off the Qt GUI thread."""
import base64
import json
import queue
import time
import zlib

import serial
from PySide6.QtCore import QThread, Signal

from image_core import FRAME_BYTES, unpack_payload
from protocol import LineDecoder, encode


def connection_hint(error):
    """Give a specific next step without claiming a cable or firmware is bad."""
    if isinstance(error, serial.SerialTimeoutException):
        return "USB 写入超时：端口已打开，但设备没有正常接收。请先安装配套接收固件；已安装则按 RESET 或重新插拔 C3 的 USB 后连接。"
    text = str(error)
    if isinstance(error, PermissionError) or any(word in text.lower() for word in ("permissionerror", "access is denied", "拒绝访问")):
        return "串口被占用或访问被拒绝：关闭 IDF monitor、Arduino 串口监视器和其他上位机，再重试。"
    return "串口连接中断或端口不可用：点击扫描确认端口，并检查 C3 自身的 USB 数据连接。"


class Simulator:
    """Exercises the same request/reply protocol without touching a COM port."""
    def __init__(self):
        self.buffer = bytearray()
        self.events = []
        self.saved = None
        self.busy_until = 0
        self.decoder = LineDecoder()

    def emit(self, delay, event):
        self.events.append((time.monotonic() + delay, encode(event)))

    def write(self, data):
        written = len(data)
        for kind, msg in self.decoder.feed(data):
            if kind != "event":
                continue
            rid, cmd = msg.get("id", 0), msg.get("cmd")
            if cmd in ("hello", "status"):
                self.emit(0, {"id": rid, "event": "hello", "protocol": 1,
                              "firmware": "SIMULATOR", "width": 104, "height": 212,
                              "busy": time.monotonic() < self.busy_until,
                              "saved": self.saved is not None, "simulated": True})
            elif cmd in ("image", "restore"):
                if time.monotonic() < self.busy_until:
                    self.emit(0, {"id": rid, "event": "error", "message": "设备正在刷新"})
                    continue
                try:
                    if cmd == "restore":
                        if self.saved is None:
                            raise ValueError("没有已保存的图片")
                        data = self.saved
                    else:
                        data = base64.b64decode(msg["data"], validate=True)
                        if (msg.get("width"), msg.get("height")) != (104, 212):
                            raise ValueError("尺寸错误")
                        if len(data) != FRAME_BYTES or f"{zlib.crc32(data):08x}" != msg["crc"]:
                            raise ValueError("长度或 CRC 错误")
                        unpack_payload(data)
                    saved = cmd == "image" and msg.get("save", False)
                    if saved:
                        self.saved = data
                    crc = f"{zlib.crc32(data):08x}"
                    self.emit(0, {"id": rid, "event": "accepted", "crc": crc, "bytes": len(data)})
                    self.emit(.2, {"id": rid, "event": "stage", "stage": "upload", "message": "模拟写入图像"})
                    self.emit(.5, {"id": rid, "event": "stage", "stage": "power_on", "message": "模拟开启升压"})
                    self.emit(.8, {"id": rid, "event": "stage", "stage": "refresh", "message": "模拟全屏刷新"})
                    self.emit(2, {"id": rid, "event": "stage", "stage": "power_off", "message": "模拟关闭升压"})
                    self.emit(2.3, {"id": rid, "event": "done", "crc": crc, "saved": saved,
                                   "elapsed_ms": 2300, "simulated": True})
                    self.busy_until = time.monotonic() + 2.3
                except (ValueError, KeyError) as exc:
                    self.emit(0, {"id": rid, "event": "error", "message": str(exc)})
            else:
                self.emit(0, {"id": rid, "event": "error", "message": "未知命令"})
        return written

    def read(self, _size):
        now = time.monotonic()
        ready = sorted((e for e in self.events if e[0] <= now), key=lambda e: e[0])
        self.events = [e for e in self.events if e[0] > now]
        if not ready:
            time.sleep(.02)
        return b"".join(e[1] for e in ready)

    def close(self):
        pass


class SerialWorker(QThread):
    received = Signal(dict)
    log = Signal(str)
    opened = Signal()
    failed = Signal(str)
    transferred = Signal(int)

    def __init__(self, port, parent=None):
        super().__init__(parent)
        self.port = port
        self.outgoing = queue.Queue()

    def send(self, message):
        self.outgoing.put(encode(message))

    def run(self):
        connection = None
        try:
            if self.port == "SIMULATOR":
                connection = Simulator()
            else:
                # Avoid the usual RTS/DTR reset pulse when opening an ESP32 port.
                connection = serial.serial_for_url(self.port, do_not_open=True)
                connection.baudrate = 115200
                connection.timeout = .05
                connection.write_timeout = 2
                connection.dtr = False
                connection.rts = False
                connection.open()
                connection.reset_input_buffer()
            self.opened.emit()
            decoder = LineDecoder()
            while not self.isInterruptionRequested():
                try:
                    packet = self.outgoing.get_nowait()
                except queue.Empty:
                    packet = None
                if packet is not None:
                    sent = 0
                    while sent < len(packet):
                        count = connection.write(packet[sent:sent+256])
                        if not count:
                            raise OSError("串口写入无进展")
                        sent += count
                        self.transferred.emit(round(sent * 100 / len(packet)))
                        if self.port != "SIMULATOR":
                            time.sleep(.003)
                for kind, value in decoder.feed(connection.read(2048)):
                    if kind == "event":
                        self.received.emit(value)
                    else:
                        self.log.emit(value)
        except Exception as exc:
            self.failed.emit(connection_hint(exc) + f"\n原始错误：{type(exc).__name__}: {exc}")
        finally:
            if connection is not None:
                connection.close()
