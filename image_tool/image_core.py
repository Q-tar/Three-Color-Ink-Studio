"""Image pipeline for E2213JS0C: two MSB-first 1-bpp pigment planes."""
from dataclasses import dataclass
from pathlib import Path
import zlib

from PIL import Image, ImageEnhance, ImageOps

WIDTH, HEIGHT = 104, 212
PLANE_BYTES = WIDTH * HEIGHT // 8
FRAME_BYTES = PLANE_BYTES * 2
RED = (210, 38, 48)


@dataclass
class Options:
    landscape: bool = False
    fill: bool = False
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0
    rotation: int = 0
    threshold: int = 180
    red_gap: int = 40
    brightness: float = 1.0
    contrast: float = 1.0
    dither: bool = False
    use_red: bool = True


@dataclass
class Converted:
    colour: Image.Image
    preview: Image.Image
    native_preview: Image.Image
    black: bytes
    red: bytes

    @property
    def payload(self):
        return self.black + self.red

    @property
    def crc(self):
        return f"{zlib.crc32(self.payload):08x}"


def load_image(path):
    # Flatten alpha on white, apply phone EXIF orientation, close file handle.
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        bg = Image.new("RGBA", image.size, "white")
        bg.alpha_composite(image)
        return bg.convert("RGB")


def pack_plane(mask):
    if mask.size != (WIDTH, HEIGHT):
        raise ValueError("屏幕图像必须是 104 × 212")
    packed = bytearray(PLANE_BYTES)
    for index, pixel in enumerate(mask.convert("L").get_flattened_data()):
        if pixel:
            packed[index // 8] |= 0x80 >> (index % 8)
    return bytes(packed)


def unpack_payload(data):
    if len(data) != FRAME_BYTES:
        raise ValueError("图像数据必须是 5512 字节")
    preview = Image.new("RGB", (WIDTH, HEIGHT), "white")
    pixels = []
    for i in range(WIDTH * HEIGHT):
        bit = 0x80 >> (i % 8)
        black = data[i // 8] & bit
        red = data[PLANE_BYTES + i // 8] & bit
        if black and red:
            raise ValueError("黑色与红色色层不能重叠")
        pixels.append((0, 0, 0) if black else RED if red else (255, 255, 255))
    preview.putdata(pixels)
    return preview


def convert(image, options):
    w, h = (HEIGHT, WIDTH) if options.landscape else (WIDTH, HEIGHT)
    source = image.rotate(options.rotation % 360, expand=True)
    source = ImageEnhance.Brightness(source).enhance(options.brightness)
    source = ImageEnhance.Contrast(source).enhance(options.contrast)
    scale = (max if options.fill else min)(w / source.width, h / source.height)
    scale *= max(1.0, min(options.zoom, 4.0))
    sw, sh = max(1, round(source.width * scale)), max(1, round(source.height * scale))
    # Clamp drag movement; fitted images retain white borders, filled images cover canvas.
    dx = max(-abs(sw - w) / 2, min(options.pan_x * w, abs(sw - w) / 2))
    dy = max(-abs(sh - h) / 2, min(options.pan_y * h, abs(sh - h) / 2))
    canvas = Image.new("RGB", (w, h), "white")
    canvas.paste(source.resize((sw, sh), Image.Resampling.LANCZOS),
                 (round((w - sw) / 2 + dx), round((h - sh) / 2 + dy)))

    red_mask = Image.new("L", (w, h))
    red_mask.putdata([
        255 if options.use_red and r >= 120 and r - g >= options.red_gap
        and r - b >= options.red_gap else 0
        for r, g, b in canvas.get_flattened_data()
    ])
    gray = canvas.convert("L")
    if options.dither:
        # Keep red pixels white during black dithering, then mask them out again.
        gray.paste(255, mask=red_mask)
        shift = 128 - options.threshold
        gray = gray.point(lambda p: max(0, min(255, p + shift)))
        bw = gray.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")
        black_mask = ImageOps.invert(bw)
    else:
        black_mask = gray.point(lambda p: 255 if p < options.threshold else 0)
    black_mask.paste(0, mask=red_mask)
    preview = Image.new("RGB", (w, h), "white")
    preview.paste((0, 0, 0), mask=black_mask)
    preview.paste(RED, mask=red_mask)
    if options.landscape:
        # User sees a landscape frame; the panel still receives its native portrait order.
        black_mask = black_mask.transpose(Image.Transpose.ROTATE_270)
        red_mask = red_mask.transpose(Image.Transpose.ROTATE_270)
        native = preview.transpose(Image.Transpose.ROTATE_270)
    else:
        native = preview.copy()
    return Converted(canvas, preview, native, pack_plane(black_mask), pack_plane(red_mask))


def export_header(result, path):
    lines = ["// E2213JS0C: 104x212, row-major, MSB first, 1=pigment, 0=white.",
             "#pragma once", "#include <stdint.h>",
             "#define EPD_IMAGE_WIDTH 104", "#define EPD_IMAGE_HEIGHT 212"]
    for name, plane in (("black", result.black), ("red", result.red)):
        lines.append(f"static const uint8_t epd_image_{name}[{PLANE_BYTES}] = {{")
        lines.extend("    " + ", ".join(f"0x{v:02x}" for v in plane[i:i+16]) + ","
                     for i in range(0, len(plane), 16))
        lines.append("};")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
