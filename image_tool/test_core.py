import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
import zlib

from PIL import Image
from image_core import (Options, convert, load_image, pack_plane, unpack_payload,
                        export_header, WIDTH, HEIGHT, PLANE_BYTES, FRAME_BYTES, RED)
from protocol import LineDecoder, encode, image_command
from device import Simulator


class ImageTests(unittest.TestCase):
    def test_pigment_polarity_and_bit_order(self):
        mask = Image.new("L", (WIDTH, HEIGHT))
        for x, y in [(0, 0), (7, 0), (8, 0), (103, 211)]:
            mask.putpixel((x, y), 255)
        packed = pack_plane(mask)
        self.assertEqual(len(packed), PLANE_BYTES)
        self.assertEqual(packed[:2], b"\x81\x80")
        self.assertEqual(packed[-1], 1)

    def test_black_red_white_roundtrip(self):
        image = Image.new("RGB", (WIDTH, HEIGHT), "white")
        image.putpixel((0, 0), (0, 0, 0))
        image.putpixel((1, 0), (255, 0, 0))
        result = convert(image, Options())
        self.assertEqual(result.black[0], 0x80)
        self.assertEqual(result.red[0], 0x40)
        self.assertEqual(result.native_preview.tobytes(), unpack_payload(result.payload).tobytes())

    def test_landscape_maps_to_native(self):
        image = Image.new("RGB", (HEIGHT, WIDTH), "white")
        image.putpixel((0, 0), (0, 0, 0))
        image.putpixel((HEIGHT-1, WIDTH-1), (255, 0, 0))
        result = convert(image, Options(landscape=True))
        native = unpack_payload(result.payload)
        self.assertEqual(result.preview.size, (212, 104))
        self.assertEqual(native.getpixel((103, 0)), (0, 0, 0))
        self.assertEqual(native.getpixel((0, 211)), RED)

    def test_dither_never_overlaps_planes(self):
        image = Image.new("RGB", (104, 212))
        image.putdata([(i % 256, (i*7) % 256, (i*13) % 256) for i in range(104*212)])
        result = convert(image, Options(dither=True))
        self.assertFalse(any(a & b for a, b in zip(result.black, result.red)))
        self.assertEqual(result.native_preview.tobytes(), unpack_payload(result.payload).tobytes())

    def test_alpha_and_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(root / "透明图.png")
            image = load_image(root / "透明图.png")
            self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
            result = convert(image, Options())
            self.assertEqual(result.payload, bytes(FRAME_BYTES))
            export_header(result, root / "epd_image.h")
            header = (root / "epd_image.h").read_text()
            self.assertEqual(header.count("0x00"), FRAME_BYTES)
            self.assertIn("EPD_IMAGE_WIDTH 104", header)

    def test_fill_and_extreme_pan_no_empty_border(self):
        result = convert(Image.new("RGB", (600, 100), "black"), Options(fill=True, pan_x=4, pan_y=-4))
        self.assertEqual(result.black, b"\xff" * PLANE_BYTES)


class ProtocolTests(unittest.TestCase):
    def test_fragmentation_and_logs(self):
        decoder = LineDecoder()
        packet = b"I (10) boot: hello\r\n" + encode({"id": 7, "event": "done", "message": "完成"})
        result = []
        for offset in range(0, len(packet), 3):
            result.extend(decoder.feed(packet[offset:offset+3]))
        self.assertEqual(result[0], ("log", "I (10) boot: hello"))
        self.assertEqual(result[1][1]["message"], "完成")

    def test_oversized_or_invalid_line_resync(self):
        decoder = LineDecoder()
        results = decoder.feed(b"x"*11000+b"\n@EPD1 null\n@EPD1 {bad}\n"+encode({"event": "hello"}))
        self.assertEqual(len([r for r in results if r[0] == "log"]), 3)
        self.assertEqual(results[-1], ("event", {"event": "hello"}))

    def test_simulated_upload_save_restore_and_crc_failure(self):
        simulator = Simulator()
        data = convert(Image.new("RGB", (104, 212), "black"), Options()).payload
        command = image_command(10, data, True)
        wire = encode(command)
        for offset in range(0, len(wire), 113):
            self.assertEqual(simulator.write(wire[offset:offset+113]), len(wire[offset:offset+113]))
        decoder = LineDecoder()
        initial = [v for k, v in decoder.feed(simulator.read(2048)) if k == "event"]
        self.assertEqual(initial[0]["event"], "accepted")
        self.assertEqual(initial[0]["crc"], f"{zlib.crc32(data):08x}")
        simulator.write(encode(image_command(11, data)))
        error = [v for k, v in decoder.feed(simulator.read(2048)) if k == "event"]
        self.assertEqual(error[0]["event"], "error")
        # Advance simulated events without waiting for an actual display cycle.
        simulator.events = [(0, line) for _, line in simulator.events]
        simulator.busy_until = 0
        events = [v for k, v in decoder.feed(simulator.read(2048)) if k == "event"]
        self.assertEqual(events[-1]["event"], "done")
        simulator.write(encode({"id": 12, "cmd": "restore"}))
        events = [v for k, v in decoder.feed(simulator.read(2048)) if k == "event"]
        self.assertEqual(events[0]["event"], "accepted")
        simulator.busy_until = 0
        bad = image_command(13, data)
        bad["crc"] = "00000000"
        simulator.write(encode(bad))
        events = [v for k, v in decoder.feed(simulator.read(2048)) if k == "event"]
        self.assertEqual(events[-1]["event"], "error")


if __name__ == "__main__":
    unittest.main()
