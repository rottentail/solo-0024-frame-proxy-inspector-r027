from __future__ import annotations

from frame_proxy.media.frames import Frame
from frame_proxy.media.ppm import encode_ppm  # reuse production PPM encoder


def render_pair_ppm(
    probe: Frame,
    source: Frame,
    *,
    probe_label: str,
    source_label: str,
    gutter: int = 6,
) -> bytes:
    """Render a side-by-side PPM linking the two actual decoded frames.

    Both tiles come from real frame decodes, not thumbnails guessed from
    signatures, so an auditor can open this artifact and compare the original
    pixels on each side. A neutral gutter separates the two clips.
    """
    target_height = max(probe.height, source.height)
    probe_scaled = _fit_height(probe, target_height)
    source_scaled = _fit_height(source, target_height)
    width = (
        probe_scaled.width + gutter + source_scaled.width
    )
    composed = bytearray(width * target_height * 3)
    gutter_color = (32, 37, 44)

    for y in range(target_height):
        row_start = y * width * 3
        _blit(probe_scaled, composed, width, y, row_start, 0)
        for gx in range(gutter):
            offset = row_start + (probe_scaled.width + gx) * 3
            composed[offset : offset + 3] = bytes(gutter_color)
        _blit(
            source_scaled,
            composed,
            width,
            y,
            row_start,
            probe_scaled.width + gutter,
        )

    return encode_ppm(width, target_height, bytes(composed))


def _fit_height(frame: Frame, target_height: int) -> Frame:
    if frame.height == target_height:
        return frame
    scale = target_height / frame.height
    target_width = max(1, round(frame.width * scale))
    return _nearest_resize(frame, target_width, target_height)


def _nearest_resize(
    frame: Frame,
    target_width: int,
    target_height: int,
) -> Frame:
    resized = bytearray(target_width * target_height * 3)
    for y in range(target_height):
        source_y = min(
            frame.height - 1,
            int(y * frame.height / target_height),
        )
        for x in range(target_width):
            source_x = min(
                frame.width - 1,
                int(x * frame.width / target_width),
            )
            source_offset = (source_y * frame.width + source_x) * 3
            target_offset = (y * target_width + x) * 3
            resized[target_offset : target_offset + 3] = frame.rgb[
                source_offset : source_offset + 3
            ]
    return Frame(
        index=frame.index,
        width=target_width,
        height=target_height,
        rgb=bytes(resized),
    )


def _blit(
    frame: Frame,
    canvas: bytearray,
    canvas_width: int,
    y: int,
    row_start: int,
    x_offset: int,
) -> None:
    for x in range(frame.width):
        source = (y * frame.width + x) * 3 if y < frame.height else 0
        target = row_start + (x_offset + x) * 3
        if y < frame.height:
            canvas[target : target + 3] = frame.rgb[source : source + 3]
