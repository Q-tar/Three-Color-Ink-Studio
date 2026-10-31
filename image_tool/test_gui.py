"""Integration checks: actual Qt event loop + threaded protocol transport, no hardware."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import asdict
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
import app as studio
from image_core import Options


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        settings = QSettings(str(Path(self.temp.name) / "settings.ini"), QSettings.IniFormat)
        with patch.object(studio, "QSettings", return_value=settings):
            self.window = studio.MainWindow(simulate=True)

    def tearDown(self):
        self.window.active_id = None
        self.window.device_busy = False
        self.window.close()
        self.qt.processEvents()
        self.temp.cleanup()

    def until(self, predicate, timeout=7):
        limit = time.monotonic() + timeout
        while time.monotonic() < limit:
            self.qt.processEvents()
            if predicate():
                return
            time.sleep(.01)
        self.fail("Timed out: " + self.window.logs.toPlainText())

    def test_reset_all_changed_options_atomically(self):
        w = self.window
        w.orientation.setCurrentIndex(1)
        w.fit.setCurrentIndex(1)
        w.rotate(90)
        for key, slider in w.sliders.items():
            slider.setValue(slider.maximum())
        w.dither.setChecked(True)
        w.red_check.setChecked(False)
        w.pan(.4, -.4)
        w.reset_options()
        self.assertEqual(asdict(w.options), asdict(Options()))
        self.assertEqual(w.result.preview.size, (104, 212))

    def test_full_threaded_connect_send_save_restore_disconnect(self):
        w = self.window
        self.assertFalse(w.send_btn.isEnabled())
        w.toggle_connection()
        self.until(lambda: w.ready)
        self.assertTrue(w.send_btn.isEnabled())
        self.assertFalse(w.restore_btn.isEnabled())
        w.orientation.setCurrentIndex(1)
        w.save_check.setChecked(True)
        w.send_image()
        sent_crc = w.active_crc
        self.assertFalse(w.send_btn.isEnabled())
        self.until(lambda: w.active_id is None)
        self.assertIn("模拟流程完成", w.status_label.text())
        self.assertEqual(w.progress.value(), 100)
        self.assertTrue(w.restore_btn.isEnabled())
        self.assertTrue(w.saved_image)
        self.assertEqual(sent_crc, w.result.crc)
        w.restore_image()
        self.until(lambda: w.active_id is None)
        self.assertEqual(w.active_crc, sent_crc)
        self.assertIn("模拟流程完成", w.status_label.text())
        self.assertTrue(w.disconnect_device())
        self.assertFalse(w.send_btn.isEnabled())
        self.assertIsNone(w.worker)

    def test_mismatched_replies_cannot_confirm_completion(self):
        w = self.window
        w.ready = True
        w.active_id = 123
        w.active_crc = "abcd1234"
        w.on_event({"event": "done", "id": 124, "crc": "abcd1234"})
        self.assertEqual(w.active_id, 123)
        w.on_event({"event": "done", "id": 123, "crc": "00000000"})
        self.assertIsNone(w.active_id)
        self.assertFalse(w.ready)
        self.assertIn("CRC", w.stage_label.text())
        self.assertFalse(w.send_btn.isEnabled())

    def test_handshake_timeout_releases_port_and_allows_firmware_install(self):
        w = self.window
        w.ports.setCurrentIndex(w.ports.findData("SIMULATOR"))
        w.ports.addItem("Test COM9", "COM9")
        w.ports.setCurrentIndex(w.ports.findData("COM9"))
        worker = MagicMock()
        worker.isRunning.return_value = True
        worker.wait.return_value = True
        w.worker = worker
        w.handshake_started = time.monotonic() - 11
        w.tick()
        worker.requestInterruption.assert_called_once()
        worker.wait.assert_called_once()
        worker.send.assert_not_called()
        self.assertIsNone(w.worker)
        self.assertIn("端口已释放", w.status_label.text())
        self.assertTrue(w.flash_btn.isEnabled())

    def test_serial_failure_keeps_specific_hint_after_disconnect(self):
        w = self.window
        worker = MagicMock()
        worker.wait.return_value = True
        w.worker = worker
        w.on_serial_error("USB 写入超时：请安装配套接收固件。\n原始错误：Write timeout")
        self.assertIsNone(w.worker)
        self.assertFalse(w.ready)
        self.assertIn("USB 写入超时", w.stage_label.text())
        self.assertIn("端口已释放", w.status_label.text())

    def test_storage_error_does_not_block_display_connection(self):
        w = self.window
        w.save_check.setChecked(True)
        w.on_event({"id": 1, "event": "hello", "protocol": 1, "width": 104,
                    "height": 212, "firmware": "EPD_BOARD2_RECEIVER_1.1",
                    "storage_available": False, "storage_error": "ESP_ERR_NVS_NO_FREE_PAGES"})
        self.assertTrue(w.ready)
        self.assertFalse(w.save_check.isChecked())
        self.assertFalse(w.save_check.isEnabled())
        self.assertIn("ESP_ERR_NVS_NO_FREE_PAGES", w.stage_label.text())


if __name__ == "__main__":
    unittest.main()
