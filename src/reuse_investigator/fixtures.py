"""Deterministic fixture footage for the reuse investigator.

This module builds uncompressed 24-bit AVI files using the *same* RIFF writer
approach as the production demo writer, but with deliberately distinctive
frame content (moving gradients, shapes, and texture) so that reuse can be
told apart from coincidence. It is test data only and never touches the
production service.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


def _chunk(tag: bytes, payload: bytes) -> bytes:
    padding = b"\x00" if len(payload) & 1 else b""
    return tag + struct.pack("<I", len(payload)) + payload + padding


def _list_chunk(kind: bytes, children: list[bytes]) -> bytes:
    return _chunk(b"LIST", kind + b"".join(children))


def _clamp(value: int) -> int:
    return max(0, min(255, value))


def _put_pixel(
    buffer: bytearray,
    width: int,
    x: int,
    y: int,
    rgb: tuple[int, int, int],
) -> None:
    offset = (y * width + x) * 3
    buffer[offset] = rgb[0]
    buffer[offset + 1] = rgb[1]
    buffer[offset + 2] = rgb[2]


def _base_pixel(x: int, y: int, t: int, seed: int) -> tuple[int, int, int]:
    """A rich, time-varying texture unique to a given scene seed."""
    wave = (x * 7 + y * 13 + t * 19 + seed * 31) % 256
    band = ((x // 8) + (y // 8) + t + seed) % 2
    red = _clamp((wave + seed * 17) % 256)
    green = _clamp((wave * 2 + 40 + seed * 23) % 256)
    blue = _clamp((y * 3 + t * 11 + seed * 29) % 256)
    if band:
        red = _clamp(red + 60)
        green = _clamp(green - 30)
    # A moving geometric shape gives strong temporal structure.
    cx = (t * 5 + seed * 20) % 96
    cy = 40 + ((t * 3 + seed * 7) % 24) - 12
    if abs(x - cx) <= 6 and abs(y - cy) <= 6:
        red, green, blue = 240, 240, 240
    return red, green, blue


def render_frame_rgb(
    width: int,
    height: int,
    t: int,
    seed: int,
) -> bytes:
    buffer = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            _put_pixel(buffer, width, x, y, _base_pixel(x, y, t, seed))
    return bytes(buffer)


def render_flat_frame(
    width: int,
    height: int,
    color: tuple[int, int, int],
) -> bytes:
    row = bytes(color) * width
    return row * height


def _to_bottom_up_bgr(
    rgb: bytes,
    width: int,
    height: int,
    row_stride: int,
) -> bytes:
    rows: list[bytes] = []
    for y in range(height - 1, -1, -1):
        row = bytearray()
        for x in range(width):
            offset = (y * width + x) * 3
            red, green, blue = rgb[offset], rgb[offset + 1], rgb[offset + 2]
            row.extend((blue, green, red))
        row.extend(b"\x00" * (row_stride - len(row)))
        rows.append(bytes(row))
    return b"".join(rows)


def build_avi(
    path: str | Path,
    frames_rgb: list[bytes],
    width: int,
    height: int,
    fps: int = 12,
) -> Path:
    row_stride = ((width * 3 + 3) // 4) * 4
    frame_size = row_stride * height
    frame_count = len(frames_rgb)

    avih = struct.pack(
        "<14I",
        1_000_000 // fps,
        frame_size * fps,
        0,
        0x10,
        frame_count,
        0,
        1,
        frame_size,
        width,
        height,
        0,
        0,
        0,
        0,
    )
    strh = struct.pack(
        "<4s4sIHHIIIIIIIIhhhh",
        b"vids",
        b"DIB ",
        0,
        0,
        0,
        0,
        1,
        fps,
        0,
        frame_count,
        frame_size,
        0,
        frame_size,
        0,
        0,
        width,
        height,
    )
    strf = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        height,
        1,
        24,
        0,
        frame_size,
        2835,
        2835,
        0,
        0,
    )
    header = _list_chunk(
        b"hdrl",
        [
            _chunk(b"avih", avih),
            _list_chunk(
                b"strl",
                [_chunk(b"strh", strh), _chunk(b"strf", strf)],
            ),
        ],
    )
    media = [
        _chunk(
            b"00db",
            _to_bottom_up_bgr(frame, width, height, row_stride),
        )
        for frame in frames_rgb
    ]
    body = b"AVI " + header + _list_chunk(b"movi", media)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return output


# ---------------------------------------------------------------------------
# Transform helpers used to simulate re-editing.
# ---------------------------------------------------------------------------


def nearest_resize(
    rgb: bytes,
    src_w: int,
    src_h: int,
    dst_w: int,
    dst_h: int,
) -> bytes:
    out = bytearray(dst_w * dst_h * 3)
    for y in range(dst_h):
        sy = min(src_h - 1, int(y * src_h / dst_h))
        for x in range(dst_w):
            sx = min(src_w - 1, int(x * src_w / dst_w))
            source = (sy * src_w + sx) * 3
            target = (y * dst_w + x) * 3
            out[target : target + 3] = rgb[source : source + 3]
    return bytes(out)


def crop(
    rgb: bytes,
    src_w: int,
    src_h: int,
    x0: int,
    y0: int,
    crop_w: int,
    crop_h: int,
) -> bytes:
    out = bytearray(crop_w * crop_h * 3)
    for y in range(crop_h):
        for x in range(crop_w):
            source = ((y0 + y) * src_w + (x0 + x)) * 3
            target = (y * crop_w + x) * 3
            out[target : target + 3] = rgb[source : source + 3]
    return bytes(out)


def color_grade(
    rgb: bytes,
    *,
    gain: tuple[float, float, float],
    offset: int,
) -> bytes:
    out = bytearray(len(rgb))
    for index in range(0, len(rgb), 3):
        out[index] = _clamp(int(rgb[index] * gain[0] + offset))
        out[index + 1] = _clamp(int(rgb[index + 1] * gain[1] + offset))
        out[index + 2] = _clamp(int(rgb[index + 2] * gain[2] + offset))
    return bytes(out)


def block_shuffle(
    rgb: bytes,
    width: int,
    height: int,
    block: int,
    t: int,
) -> bytes:
    """Permute spatial blocks while preserving the coarse luma distribution.

    This keeps the global histogram (and therefore coarse grid-MAD) similar
    enough to trip retrieval, but destroys the spatial correlation that NCC
    measures, so verification must reject it. The permutation is deterministic.
    """
    cols = (width + block - 1) // block
    rows = (height + block - 1) // block
    order = list(range(cols * rows))
    # Deterministic Fisher-Yates driven by the frame time.
    a = 1103515245
    c = 12345
    seed = (t + 1) * 2654435761 & 0xFFFFFFFF
    for index in range(len(order) - 1, 0, -1):
        seed = (a * seed + c) & 0x7FFFFFFF
        target = seed % (index + 1)
        order[index], order[target] = order[target], order[index]
    permuted = [order[position] for position in range(len(order))]

    out = bytearray(width * height * 3)
    for dest in range(cols * rows):
        source = permuted[dest]
        dx, dy = dest % cols, dest // cols
        sx, sy = source % cols, source // cols
        for by in range(block):
            for bx in range(block):
                px = dx * block + bx
                py = dy * block + by
                qx = sx * block + bx
                qy = sy * block + by
                if px >= width or py >= height or qx >= width or qy >= height:
                    continue
                source_offset = (qy * width + qx) * 3
                target_offset = (py * width + px) * 3
                out[target_offset : target_offset + 3] = rgb[
                    source_offset : source_offset + 3
                ]
    return bytes(out)


def render_stripe_frame(
    width: int,
    height: int,
    t: int,
) -> bytes:
    """High-frequency moving stripe content with no counterpart in sources."""
    buffer = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            value = 255 if ((x + t * 9) % 7) < 3 else 12
            cross = 255 if ((y - t * 5) % 11) < 2 else 0
            rgb = (
                _clamp(value - cross),
                _clamp(cross + 40),
                _clamp(255 - value),
            )
            _put_pixel(buffer, width, x, y, rgb)
    return bytes(buffer)


@dataclass(frozen=True, slots=True)
class FixtureSet:
    source_paths: dict[str, Path]
    probe_path: Path
    # Ground truth expressed as (probe half-open range, source clip, source
    # half-open range, direction). Empty source means genuinely new footage.
    ground_truth: list[tuple[tuple[int, int], str, tuple[int, int], str]]
