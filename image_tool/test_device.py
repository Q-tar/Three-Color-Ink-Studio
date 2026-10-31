"""Exercise real serial-worker failure paths without opening a hardware port."""
import unittest
from unittest.mock import MagicMock, patch

import serial

from device import SerialWorker, connection_hint


class SerialFailureTests(unittest.TestCase):
    def test_busy_and_unresponsive_devices_have_distinct_instructions(self):
        busy = serial.SerialException("could not open port 'COM9': PermissionError(13, '拒绝访问。')")
        self.assertIn("被占用", connection_hint(busy))
        self.assertIn("接收固件", connection_hint(serial.SerialTimeoutException("Write timeout")))

    def test_write_timeout_closes_port_and_reports_firmware_next_step(self):
        port = MagicMock()
        port.write.side_effect = serial.SerialTimeoutException("Write timeout")
        worker = SerialWorker("COM9")
        errors = []
        worker.failed.connect(errors.append)
        worker.send({"id": 1, "cmd": "hello"})
        with patch("device.serial.serial_for_url", return_value=port):
            worker.run()
        port.open.assert_called_once()
        port.close.assert_called_once()
        self.assertFalse(port.dtr)
        self.assertFalse(port.rts)
        self.assertEqual(len(errors), 1)
        self.assertIn("接收固件", errors[0])
        self.assertIn("Write timeout", errors[0])


if __name__ == "__main__":
    unittest.main()
