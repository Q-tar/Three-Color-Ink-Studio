"""Versioned line protocol. Device logs can coexist with prefixed JSON replies."""
import base64
import json
import zlib

from image_core import WIDTH, HEIGHT, FRAME_BYTES

PREFIX = b"@EPD1 "
MAX_LINE = 10000


def encode(message):
    line = PREFIX + json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
    if len(line) > MAX_LINE:
        raise ValueError("协议数据过长")
    return line


def image_command(request_id, payload, save=False):
    if len(payload) != FRAME_BYTES:
        raise ValueError("图像长度错误")
    return {"id": request_id, "cmd": "image", "width": WIDTH, "height": HEIGHT,
            "crc": f"{zlib.crc32(payload):08x}", "save": bool(save),
            "data": base64.b64encode(payload).decode("ascii")}


class LineDecoder:
    def __init__(self):
        self.buffer = bytearray()
        self.discarding = False

    def feed(self, chunk):
        """Yield ('event', dict) or ('log', text); discard oversized lines to next newline."""
        out = []
        for byte in chunk:
            if byte == 10:
                if self.discarding:
                    out.append(("log", "接收行过长，已丢弃"))
                else:
                    line = bytes(self.buffer).rstrip(b"\r")
                    if line.startswith(PREFIX):
                        try:
                            event = json.loads(line[len(PREFIX):])
                            if not isinstance(event, dict):
                                raise ValueError("reply is not an object")
                            out.append(("event", event))
                        except (ValueError, UnicodeDecodeError):
                            out.append(("log", "忽略无效的协议回复"))
                    elif line:
                        out.append(("log", line.decode("utf-8", errors="replace")))
                self.buffer.clear()
                self.discarding = False
            elif not self.discarding:
                if len(self.buffer) >= MAX_LINE:
                    self.buffer.clear()
                    self.discarding = True
                else:
                    self.buffer.append(byte)
        return out
