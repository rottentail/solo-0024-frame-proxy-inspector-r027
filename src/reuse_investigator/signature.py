from __future__ import annotations

import math

from frame_proxy.media.frames import Frame

from reuse_investigator.config import InvestigatorSettings
from reuse_investigator.domain import FrameSignature


def _luma(red: int, green: int, blue: int) -> float:
    return (299 * red + 587 * green + 114 * blue) / 1000.0


def _cell_bounds(index: int, extent: int, grid_size: int) -> tuple[int, int]:
    # Map a grid cell to a half-open pixel span using exact area weights, so
    # the descriptor is independent of the source frame dimensions. This is
    # what makes scaled or cropped re-encodes land on the same coarse grid.
    start = (index * extent) // grid_size
    end = ((index + 1) * extent) // grid_size
    if end <= start:
        end = min(extent, start + 1)
    return start, end


def signature_for_frame(
    frame: Frame,
    frame_index: int,
    settings: InvestigatorSettings,
) -> FrameSignature:
    grid_size = settings.grid_size
    pixels = frame.rgb
    width = frame.width
    height = frame.height

    red_total = 0
    green_total = 0
    blue_total = 0
    pixel_count = width * height
    for offset in range(0, pixel_count * 3, 3):
        red_total += pixels[offset]
        green_total += pixels[offset + 1]
        blue_total += pixels[offset + 2]
    mean_red = red_total / pixel_count
    mean_green = green_total / pixel_count
    mean_blue = blue_total / pixel_count

    cells: list[int] = []
    for row in range(grid_size):
        y0, y1 = _cell_bounds(row, height, grid_size)
        for col in range(grid_size):
            x0, x1 = _cell_bounds(col, width, grid_size)
            luma_sum = 0.0
            samples = 0
            for y in range(y0, y1):
                row_start = (y * width + x0) * 3
                row_end = (y * width + x1) * 3
                for offset in range(row_start, row_end, 3):
                    luma_sum += _luma(
                        pixels[offset],
                        pixels[offset + 1],
                        pixels[offset + 2],
                    )
                    samples += 1
            cells.append(round(luma_sum / max(1, samples)))

    grid_mean = sum(cells) / len(cells)
    variance = sum((value - grid_mean) ** 2 for value in cells) / len(cells)

    bits = 0
    for index, value in enumerate(cells):
        if value >= grid_mean:
            bits |= 1 << index

    return FrameSignature(
        frame_index=frame_index,
        grid=tuple(cells),
        bits=bits,
        mean_red=mean_red,
        mean_green=mean_green,
        mean_blue=mean_blue,
        grid_variance=variance,
    )


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def mean_absolute_grid_distance(
    left: FrameSignature,
    right: FrameSignature,
) -> float:
    """Mean absolute difference of the area-averaged luma grids.

    Because both grids describe the same normalized 8x8 layout, this is
    tolerant of scaling, cropping, and global brightness/contrast changes,
    making it a better coarse retrieval signal than the thresholded bits.
    """
    return sum(abs(a - b) for a, b in zip(left.grid, right.grid)) / len(left.grid)


def normalized_cross_correlation(
    left: tuple[int, ...],
    right: tuple[int, ...],
) -> float:
    """Geometry-normalized NCC over the luma grids.

    A constant reference frame has zero variance and cannot support a
    correlation; callers treat that case as flat and never confirm on it.
    """
    if len(left) != len(right) or not left:
        raise ValueError("grids must be non-empty and aligned")
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = 0.0
    left_energy = 0.0
    right_energy = 0.0
    for a, b in zip(left, right):
        da = a - left_mean
        db = b - right_mean
        numerator += da * db
        left_energy += da * da
        right_energy += db * db
    if left_energy <= 0.0 or right_energy <= 0.0:
        return 0.0
    return numerator / math.sqrt(left_energy * right_energy)


def normalized_color_distance(
    left: FrameSignature,
    right: FrameSignature,
) -> float:
    """Color invariant distance after per-frame mean normalization.

    Dividing by each frame's own luma mean neutralizes a global color grade
    or brightness offset while preserving the relative R/G/B balance.
    """
    left_luma = (left.mean_red + left.mean_green + left.mean_blue) / 3.0
    right_luma = (right.mean_red + right.mean_green + right.mean_blue) / 3.0
    if left_luma <= 1.0 or right_luma <= 1.0:
        return 1.0
    ratios_left = (
        left.mean_red / left_luma,
        left.mean_green / left_luma,
        left.mean_blue / left_luma,
    )
    ratios_right = (
        right.mean_red / right_luma,
        right.mean_green / right_luma,
        right.mean_blue / right_luma,
    )
    delta = math.sqrt(
        sum((a - b) ** 2 for a, b in zip(ratios_left, ratios_right))
    )
    # Ratios live near 1.0; a delta of ~0.6 is already a severe mismatch.
    return min(1.0, delta / 0.6)
