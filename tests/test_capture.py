"""仅在无网络工具容器中验证屏幕 PNG，不启动 Wine 或读取账户。"""

import os
from pathlib import Path
import struct
import subprocess
import time
import zlib


def test_capture_preserves_rgb_and_complete_png(tmp_path: Path) -> None:
    env = dict(os.environ, DISPLAY=":198")
    server = subprocess.Popen(["Xvfb", ":198", "-screen", "0", "64x48x24", "-noreset",
                               "-nolisten", "tcp", "-extension", "GLX"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while True:
            result = subprocess.run(["xsetroot", "-solid", "#12ab34"], env=env,
                                    capture_output=True, timeout=2)
            if result.returncode == 0:
                break
            assert server.poll() is None and time.monotonic() < deadline, "测试显示服务未就绪"
            time.sleep(.05)
        target = tmp_path / "screen.png"
        subprocess.run(["cfb-capture", str(target)], env=env, capture_output=True, timeout=3, check=True)
        data = target.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        offset = 8
        chunks: list[tuple[bytes, bytes]] = []
        while offset < len(data):
            length = struct.unpack(">I", data[offset:offset + 4])[0]
            kind, body = data[offset + 4:offset + 8], data[offset + 8:offset + 8 + length]
            crc = struct.unpack(">I", data[offset + 8 + length:offset + 12 + length])[0]
            assert zlib.crc32(kind + body) == crc
            chunks.append((kind, body))
            offset += length + 12
        assert chunks[0] == (b"IHDR", struct.pack(">IIBBBBB", 64, 48, 8, 2, 0, 0, 0))
        assert chunks[-1] == (b"IEND", b"")
        raw = zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT"))
        assert len(raw) == 193 * 48
        prior = bytearray(192)
        for y in range(48):
            mode, row = raw[y * 193], bytearray(raw[y * 193 + 1:(y + 1) * 193])
            assert 0 <= mode <= 4
            for i in range(192):
                left, above, corner = row[i - 3] if i >= 3 else 0, prior[i], prior[i - 3] if i >= 3 else 0
                predicted = left + above - corner
                distances = [abs(predicted - left), abs(predicted - above), abs(predicted - corner)]
                paeth = [left, above, corner][distances.index(min(distances))]
                row[i] = (row[i] + [0, left, above, (left + above) // 2, paeth][mode]) & 255
            assert row == bytes.fromhex("12ab34") * 64
            prior = row
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_capture_rejects_missing_display(tmp_path: Path) -> None:
    target = tmp_path / "screen.png"
    result = subprocess.run(["cfb-capture", str(target)], env=dict(os.environ, DISPLAY=":61998"),
                            capture_output=True, timeout=3)
    assert result.returncode == 1
    assert b"X11 display unavailable" in result.stderr
    assert not target.exists()
