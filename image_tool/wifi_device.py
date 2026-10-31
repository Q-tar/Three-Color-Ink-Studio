"""HTTP transport for the C3 SoftAP. A timed-out upload is never retried."""
import json
import queue
import secrets
import urllib.parse
import urllib.request

from PySide6.QtCore import QThread, Signal
from protocol import encode


class WifiWorker(QThread):
    received = Signal(dict)
    log = Signal(str)
    opened = Signal()
    failed = Signal(str)
    transferred = Signal(int)

    def __init__(self, address, parent=None):
        super().__init__(parent)
        self.address = address.strip().rstrip("/")
        self.client = secrets.token_hex(16)
        self.outgoing = queue.Queue()

    def send(self, message):
        self.outgoing.put(encode(message))

    def request(self, path, data=None):
        request = urllib.request.Request(self.address + path, data=data,
                                         headers={"Content-Type": "application/octet-stream"})
        # Direct LAN access: do not send device requests to a system HTTP proxy.
        with self.opener.open(request, timeout=3) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("设备响应过长")
        return json.loads(raw)

    def run(self):
        uploading = False
        try:
            if "://" not in self.address:
                self.address = "http://" + self.address
            url = urllib.parse.urlsplit(self.address)
            if (url.scheme != "http" or not url.hostname or url.username or url.password
                    or url.path or url.query or url.fragment):
                raise ValueError("请输入设备 IP 或 http://IP:端口，例如 192.168.4.1")
            self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            query = "?client=" + self.client
            initial = self.request("/api/events" + query)
            cursor, boot = initial["cursor"], initial["boot"]
            self.opened.emit()
            while not self.isInterruptionRequested():
                try:
                    packet = self.outgoing.get_nowait()
                except queue.Empty:
                    packet = None
                if packet is not None:
                    uploading = True
                    response = self.request("/api/command" + query, packet)
                    if response.get("ok") is not True:
                        raise ValueError("设备未确认 HTTP 请求")
                    uploading = False
                    self.transferred.emit(100)
                batch = self.request("/api/events" + query + "&after=" + str(cursor))
                if batch.get("boot") != boot:
                    raise ValueError("设备已重启，上一项刷新结果未知")
                if batch.get("lost"):
                    raise ValueError("设备进度记录已过期，上一项刷新结果未知")
                if not isinstance(batch.get("events"), list) or batch["cursor"] < cursor:
                    raise ValueError("设备进度响应无效")
                cursor = batch["cursor"]
                for event in batch["events"]:
                    if not isinstance(event, dict):
                        raise ValueError("设备事件无效")
                    self.received.emit(event)
                # Bounded idle wait; interruption remains responsive.
                for _ in range(15):
                    if self.isInterruptionRequested():
                        return
                    self.msleep(20)
        except Exception as exc:
            uncertain = "请求可能已被设备接收，刷新结果未知；不会自动重发。" if uploading else "断线不会取消已开始的刷新。"
            self.failed.emit("Wi-Fi 连接失败：确认电脑已连接 InkStudio-C3 热点，地址为 192.168.4.1，并安装新版接收固件。"
                             + uncertain + f"\n原始错误：{type(exc).__name__}: {exc}")
