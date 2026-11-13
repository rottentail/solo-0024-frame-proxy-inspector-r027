"""Unit tests for temporal chain assembly and determination logic."""

from __future__ import annotations

import unittest
from pathlib import Path

from reuse_investigator.config import InvestigatorSettings
from reuse_investigator.domain import FrameSignature
from reuse_investigator.matching import MatchingEngine, SourceLibrary


def _sig(
    index: int,
    grid: tuple[int, ...],
    *,
    means: tuple[float, float, float] = (120.0, 100.0, 90.0),
    variance: float = 400.0,
) -> FrameSignature:
    mean = sum(grid) / len(grid)
    bits = 0
    for position, value in enumerate(grid):
        if value >= mean:
            bits |= 1 << position
    return FrameSignature(
        frame_index=index,
        grid=grid,
        bits=bits,
        mean_red=means[0],
        mean_green=means[1],
        mean_blue=means[2],
        grid_variance=variance,
    )


def _sliding_grid(base: int, step: int) -> tuple[int, ...]:
    return tuple(base + ((i * step) % 37) for i in range(64))


class MatchingEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = InvestigatorSettings(data_root=Path("/tmp/unused"))
        self.engine = MatchingEngine(self.settings)

    def test_forward_chain_is_confirmed_without_alternatives(self) -> None:
        # Source frames 4..9 and probe frames 0..4 share the same grid walk.
        source = [_sig(4 + i, _sliding_grid(60, 3 + i)) for i in range(6)]
        probe = [_sig(i, _sliding_grid(60, 3 + i)) for i in range(5)]
        library = SourceLibrary(("src",), {"src": source})
        chains, intervals, outcomes = self.engine.investigate(probe, library)
        self.assertEqual(len(chains), 1)
        chain = chains[0]
        self.assertEqual(chain.status, "confirmed")
        self.assertEqual(chain.direction, "forward")
        self.assertEqual((chain.probe_start, chain.probe_end), (0, 5))
        self.assertEqual(chain.source_clip_id, "src")
        self.assertEqual(intervals, [])

    def test_reversed_chain_direction(self) -> None:
        # Probe walks source frames 8 down to 4.
        source = [_sig(i, _sliding_grid(50, 2 + i)) for i in range(10)]
        probe = [_sig(i, _sliding_grid(50, 2 + (8 - i))) for i in range(5)]
        library = SourceLibrary(("src",), {"src": source})
        chains, _intervals, _outcomes = self.engine.investigate(probe, library)
        self.assertTrue(any(chain.direction == "reversed" for chain in chains))

    def test_near_duplicate_sources_stay_ambiguous(self) -> None:
        # Two clips with effectively identical grids and color means.
        grid_a = [_sig(i, _sliding_grid(70, 4 + i)) for i in range(6)]
        grid_b = [
            _sig(i, tuple(value + 1 for value in _sliding_grid(70, 4 + i)))
            for i in range(6)
        ]
        probe = [_sig(i, _sliding_grid(70, 4 + i)) for i in range(5)]
        library = SourceLibrary(("a", "b"), {"a": grid_a, "b": grid_b})
        chains, _intervals, _outcomes = self.engine.investigate(probe, library)
        self.assertTrue(chains)
        winner = chains[0]
        self.assertEqual(winner.status, "ambiguous")
        self.assertTrue(winner.alternatives)

    def test_no_candidate_is_unexplained(self) -> None:
        source = [_sig(i, _sliding_grid(30, 5)) for i in range(4)]
        # Probe grids far outside the retrieval MAD gate.
        probe = [_sig(i, tuple(250 - (j % 5) for j in range(64))) for i in range(3)]
        library = SourceLibrary(("src",), {"src": source})
        chains, intervals, outcomes = self.engine.investigate(probe, library)
        self.assertEqual(chains, [])
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0].probe_start, 0)
        self.assertEqual(intervals[0].probe_end, 3)
        self.assertTrue(all(o.no_candidate for o in outcomes.values()))

    def test_flat_probe_frames_never_confirm(self) -> None:
        source = [_sig(i, _sliding_grid(60, 3 + i)) for i in range(5)]
        flat_grid = tuple([40] * 64)
        probe = [
            _sig(i, flat_grid, means=(40.0, 40.0, 40.0), variance=0.0)
            for i in range(3)
        ]
        library = SourceLibrary(("src",), {"src": source})
        chains, _intervals, outcomes = self.engine.investigate(probe, library)
        self.assertEqual(chains, [])
        self.assertTrue(all(outcome.no_candidate for outcome in outcomes.values()))

    def test_held_frames_cannot_fabricate_continuity(self) -> None:
        # Distinct source frames, but the probe holds one frame three times.
        source = [_sig(i, _sliding_grid(40, 5 + i)) for i in range(8)]
        held_grid = _sliding_grid(40, 8)  # corresponds to source frame 3
        probe = [
            _sig(0, held_grid),
            _sig(1, held_grid),
            _sig(2, held_grid),
            _sig(3, _sliding_grid(40, 11)),
        ]
        library = SourceLibrary(("src",), {"src": source})
        chains, _intervals, outcomes = self.engine.investigate(probe, library)
        # The repeated frames must be marked and excluded from chain motion.
        self.assertTrue(
            all(outcomes[frame].repeated for frame in (1, 2))
        )
        # No chain may span the held run as if the source were advancing.
        for chain in chains:
            self.assertFalse(
                chain.probe_start <= 1
                and chain.probe_end >= 3
                and chain.direction == "forward"
            )


if __name__ == "__main__":
    unittest.main()
