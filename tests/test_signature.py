"""Unit tests for the signature metrics and their transform tolerance."""

from __future__ import annotations

import unittest

from frame_proxy.media.frames import Frame

from reuse_investigator.config import InvestigatorSettings
from reuse_investigator.signature import (
    hamming_distance,
    mean_absolute_grid_distance,
    normalized_color_distance,
    normalized_cross_correlation,
    signature_for_frame,
)


def _gradient_frame(width: int, height: int, *, shift: int = 0) -> Frame:
    rgb = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 3
            # Smooth spatial gradient so the 8x8 area averages are stable
            # across frame dimensions (no fast modulo aliasing).
            value = (x * 200 // max(1, width) + y * 40 // max(1, height) + shift) % 256
            rgb[offset] = value
            rgb[offset + 1] = (value + 40) % 256
            rgb[offset + 2] = (255 - value) % 256
    return Frame(index=0, width=width, height=height, rgb=bytes(rgb))


def _flat_frame(width: int, height: int, level: int) -> Frame:
    return Frame(
        index=0,
        width=width,
        height=height,
        rgb=bytes((level, level, level)) * (width * height),
    )


class SignatureMetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = InvestigatorSettings(data_root=__import__("pathlib").Path("/tmp/unused"))

    def test_grid_is_dimension_invariant_under_resize(self) -> None:
        large = _gradient_frame(96, 64)
        small = _gradient_frame(80, 56)
        left = signature_for_frame(large, 0, self.settings)
        right = signature_for_frame(small, 0, self.settings)
        # Same underlying gradient at different sizes stays close in grid MAD.
        self.assertLess(mean_absolute_grid_distance(left, right), 20.0)
        self.assertGreater(normalized_cross_correlation(left.grid, right.grid), 0.9)

    def test_global_brightness_shift_is_color_invariant(self) -> None:
        def graded(scale: float) -> Frame:
            width = height = 64
            rgb = bytearray(width * height * 3)
            for y in range(height):
                for x in range(width):
                    offset = (y * width + x) * 3
                    base = (x * 200 // width + y * 40 // height)
                    # A pure multiplicative grade keeps channel ratios fixed.
                    rgb[offset] = min(255, int(base * scale))
                    rgb[offset + 1] = min(255, int((base + 40) * scale))
                    rgb[offset + 2] = min(255, int((200 - base // 2) * scale))
            return Frame(0, width, height, bytes(rgb))

        base = signature_for_frame(graded(1.0), 0, self.settings)
        graded_sig = signature_for_frame(graded(1.3), 0, self.settings)
        # Mean-normalized color distance should stay small under a pure grade.
        self.assertLess(normalized_color_distance(base, graded_sig), 0.15)

    def test_flat_frame_has_zero_correlation(self) -> None:
        flat = signature_for_frame(_flat_frame(64, 64, 50), 0, self.settings)
        textured = signature_for_frame(
            _gradient_frame(64, 64), 0, self.settings
        )
        self.assertEqual(normalized_cross_correlation(flat.grid, textured.grid), 0.0)
        self.assertLess(flat.grid_variance, self.settings.flat_variance_floor)

    def test_hamming_is_a_metric(self) -> None:
        self.assertEqual(hamming_distance(0b1010, 0b1010), 0)
        self.assertEqual(hamming_distance(0b1010, 0b0101), 4)

    def test_unrelated_content_is_far_in_ncc(self) -> None:
        frame_a = signature_for_frame(
            _gradient_frame(64, 64, shift=0), 0, self.settings
        )
        frame_b = signature_for_frame(
            _gradient_frame(64, 64, shift=128), 0, self.settings
        )
        # Strongly shifted pattern should not reach the confirmation NCC gate.
        self.assertLess(
            normalized_cross_correlation(frame_a.grid, frame_b.grid),
            self.settings.verify_ncc_strong,
        )


if __name__ == "__main__":
    unittest.main()
