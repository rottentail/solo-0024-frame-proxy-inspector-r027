from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class InvestigatorSettings:
    """Configuration for the standalone reuse investigation workspace.

    The data root is intentionally separate from the production
    ``FRAME_PROXY_DATA_DIR`` so investigation artifacts can never be mistaken
    for service state.
    """

    data_root: Path
    grid_size: int = 8
    # Frames are streamed one at a time; these caps bound transient buffers.
    max_decode_width: int = 1920
    max_decode_height: int = 1080
    max_frame_count: int = 3000
    # Coarse retrieval gate on the mean absolute luma-grid difference. A hit
    # here is only a candidate, never a verified source. The threshold is
    # deliberately permissive enough to admit crop/resize/grade transforms.
    retrieval_mad_gate: float = 24.0
    retrieval_hamming_radius: int = 26
    # Number of retrieval candidates kept per probe frame before verification.
    retrieval_candidate_cap: int = 12
    # Verification gates on normalized cross correlation and color invariants.
    verify_ncc_strong: float = 0.65
    verify_ncc_weak: float = 0.55
    verify_color_gate: float = 0.45
    # A temporal chain must cover at least this many consecutive probe frames
    # before it can be promoted beyond a weak candidate.
    min_chain_frames: int = 3
    # Frames whose luma grid variance stays below this are treated as flat /
    # low-texture and are never allowed to confirm a unique source.
    flat_variance_floor: float = 18.0
    # Two consecutive probe frames whose grid MAD is at or below this value are
    # treated as a held/repeated frame, which cannot establish unique temporal
    # continuity on its own.
    repeated_frame_mad: float = 1.5
    # Margin between the best and runner-up chain score required for a unique
    # determination; below it the finding stays ambiguous.
    uniqueness_margin: float = 0.08
    # Maximum number of full frames held in memory at any point. Decoding is
    # streaming, so this stays a small, explicit bound.
    resident_frame_budget: int = 4

    @classmethod
    def from_env(cls) -> "InvestigatorSettings":
        data_root = Path(
            os.environ.get(
                "REUSE_INVESTIGATOR_DATA_DIR",
                ".reuse-investigator-data",
            )
        ).expanduser()
        return cls(data_root=data_root)

    def ensure_ready(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
