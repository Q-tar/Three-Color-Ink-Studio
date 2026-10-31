"""Real HTTP sockets + Qt threads, with an emulated device instead of hardware."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit, parse_qs

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from app import MainWindow
from device import Simulator
from protocol import LineDecoder, image_command
from wifi_device import WifiWorker


@contextmanager
def receiver(drop_ack=False):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            self.server.posts += 1
            packet = self.rfile.read(int(self.headers["Content-Length"]))
            client = parse_qs(urlsplit(self.path).query)["client"][0]
            now = time.monotonic()
            self.server.sim.write(packet)
            # Preserve real simulator validation; accelerate its panel delay.
            for due, data in self.server.sim.events:
                for kind, event in LineDecoder().feed(data):
                    if kind == "event":
                        self.server.pending.append((now + max(0, due-now)/20, client, event))
            self.server.sim.events.clear()
            self.server.sim.busy_until = now + .12
            if self.server.drop_ack:
                self.close_connection = True
                return
            self.respond({"ok": True})

        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            client = query["client"][0]
            now = time.monotonic()
            pending = []
            for due, owner, event in self.server.pending:
                if due <= now:
                    self.server.events.append((owner, event))
                else:
                    pending.append((due, owner, event))
            self.server.pending = pending
            after = int(query.get("after", [len(self.server.events)])[0])
            self.respond({"cursor": len(self.server.events), "boot": self.server.boot,
                          "lost": self.server.lost,
                          "events": [e for owner, e in self.server.events[after:] if owner == client]})

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.sim = Simulator()
    server.events, server.pending = [], []
    server.boot, server.lost, server.posts = "boot-one", False, 0
    server.drop_ack = drop_ack
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, "http://127.0.0.1:" + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


class WifiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def until(self, predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.qt.processEvents()
            if predicate():
                return
            time.sleep(.01)
        self.fail("HTTP/Qt flow timed out")

    def stop(self, worker):
        worker.requestInterruption()
        self.assertTrue(worker.wait(6500))
        self.qt.processEvents()

    def test_wifi_window_upload_save_restore_and_mode_controls(self):
        with receiver() as (server, address), tempfile.TemporaryDirectory() as temp:
            settings = QSettings(str(Path(temp)/"ui.ini"), QSettings.IniFormat)
            w = MainWindow(settings=settings)
            try:
                w.transport.setCurrentIndex(1)
                w.wifi_address.setText(address)
                self.assertFalse(w.flash_btn.isEnabled())
                self.assertTrue(w.ports.isHidden())
                w.toggle_connection()
                self.until(lambda: w.ready)
                self.assertFalse(w.transport.isEnabled())
                w.save_check.setChecked(True)
                w.send_image()
                crc = w.active_crc
                self.until(lambda: w.active_id is None)
                self.assertEqual(w.progress.value(), 100)
                self.assertTrue(w.saved_image)
                self.assertEqual(w.active_crc, crc)
                w.restore_image()
                self.until(lambda: w.active_id is None)
                self.assertEqual(w.active_crc, crc)
                self.assertTrue(w.disconnect_device())
                self.assertTrue(w.transport.isEnabled())
                self.assertEqual(server.posts, 3)  # hello + image + restore
            finally:
                w.active_id = None
                w.device_busy = False
                w.close()

    def test_dropped_post_response_is_not_retried(self):
        with receiver(drop_ack=True) as (server, address):
            worker = WifiWorker(address)
            errors = []
            worker.failed.connect(errors.append)
            worker.send(image_command(11, bytes(5512)))
            worker.start()
            try:
                self.until(lambda: bool(errors))
                self.assertIn("结果未知", errors[0])
                self.assertIn("不会自动重发", errors[0])
                self.assertEqual(server.posts, 1)
            finally:
                self.stop(worker)

    def test_reboot_and_lost_events_fail_instead_of_confirming_completion(self):
        for mode in ("reboot", "lost"):
            with self.subTest(mode=mode), receiver() as (server, address):
                worker = WifiWorker(address)
                errors, opened = [], []
                worker.opened.connect(lambda: opened.append(True))
                worker.failed.connect(errors.append)
                worker.start()
                try:
                    self.until(lambda: bool(opened))
                    if mode == "reboot":
                        server.boot = "boot-two"
                    else:
                        server.lost = True
                    self.until(lambda: bool(errors))
                    self.assertIn("结果未知", errors[0])
                    self.assertEqual(server.posts, 0)
                finally:
                    self.stop(worker)

    def test_bad_crc_is_reported_and_other_client_events_are_not_delivered(self):
        with receiver() as (server, address):
            worker = WifiWorker(address)
            events, errors = [], []
            worker.received.connect(events.append)
            worker.failed.connect(errors.append)
            message = image_command(51, bytes(5512))
            message["crc"] = "12345678"
            worker.send(message)
            worker.start()
            try:
                self.until(lambda: bool(events) or bool(errors))
                self.assertFalse(errors)
                self.assertEqual(events[-1]["event"], "error")
                self.assertIn("CRC", events[-1]["message"])
                server.events.append(("different-client", {"event": "done", "id": 51}))
                worker.send({"id": 52, "cmd": "hello"})
                self.until(lambda: any(e.get("event") == "hello" for e in events))
                self.assertFalse(any(e.get("event") == "done" for e in events))
            finally:
                self.stop(worker)


if __name__ == "__main__":
    unittest.main()
