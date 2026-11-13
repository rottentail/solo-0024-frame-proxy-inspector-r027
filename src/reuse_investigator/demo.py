"""Assemble the canonical re-edit demonstration set.

The set is engineered so a reviewer can independently confirm all three
outcomes the tool must distinguish:

1. a correct, unique match (including a reversed and a repeated reuse);
2. a retrieval hit that verification must reject (a look-alike decoy);
3. an interval that cannot be uniquely determined (near-duplicate sources and
   flat/static frames), plus genuinely unexplained new footage.
"""

from __future__ import annotations

from pathlib import Path

from reuse_investigator.fixtures import (
    FixtureSet,
    block_shuffle,
    build_avi,
    color_grade,
    crop,
    nearest_resize,
    render_flat_frame,
    render_frame_rgb,
    render_stripe_frame,
)

SOURCE_WIDTH = 96
SOURCE_HEIGHT = 64
PROBE_WIDTH = 80
PROBE_HEIGHT = 56
FPS = 12


def _source_frames(seed: int, count: int) -> list[bytes]:
    return [
        render_frame_rgb(SOURCE_WIDTH, SOURCE_HEIGHT, t, seed)
        for t in range(count)
    ]


def build_fixture_set(output_dir: str | Path) -> FixtureSet:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)

    # --- Sources ---------------------------------------------------------
    # source-a and source-b share seed 0: deliberately near-duplicate texture
    # so a reused segment cannot always be uniquely attributed.
    a_frames = _source_frames(seed=0, count=14)
    b_frames = _source_frames(seed=0, count=14)
    # Tiny per-pixel divergence keeps them distinct yet highly similar.
    b_frames = [
        color_grade(frame, gain=(1.02, 0.99, 1.01), offset=2)
        for frame in b_frames
    ]
    c_frames = _source_frames(seed=5, count=14)
    # The library's source-decoy holds spatially *shuffled* copies of a private
    # seed-7 roll. The probe instead uses the unshuffled originals (see below).
    # Both share the same coarse luma distribution, so retrieval surfaces
    # source-decoy candidates, but the block permutation destroys spatial
    # correlation and verification (NCC) must reject every candidate.
    decoy_private = [
        render_frame_rgb(SOURCE_WIDTH, SOURCE_HEIGHT, t, seed=7)
        for t in range(8)
    ]
    decoy_frames = [
        color_grade(
            block_shuffle(
                frame,
                SOURCE_WIDTH,
                SOURCE_HEIGHT,
                block=8,
                t=t,
            ),
            gain=(1.05, 1.05, 1.05),
            offset=-6,
        )
        for t, frame in enumerate(decoy_private)
    ]

    source_paths = {
        "source-a": build_avi(root / "source-a.avi", a_frames,
                              SOURCE_WIDTH, SOURCE_HEIGHT, FPS),
        "source-b": build_avi(root / "source-b.avi", b_frames,
                              SOURCE_WIDTH, SOURCE_HEIGHT, FPS),
        "source-c": build_avi(root / "source-c.avi", c_frames,
                              SOURCE_WIDTH, SOURCE_HEIGHT, FPS),
        "source-decoy": build_avi(root / "source-decoy.avi", decoy_frames,
                                  SOURCE_WIDTH, SOURCE_HEIGHT, FPS),
    }

    # --- Probe: a re-edited sequence ------------------------------------
    probe_frames: list[bytes] = []
    ground_truth: list[tuple[tuple[int, int], str, tuple[int, int], str]] = []

    def add(
        frames: list[bytes],
        source: str,
        source_range: tuple[int, int],
        direction: str,
    ) -> None:
        start = len(probe_frames)
        probe_frames.extend(frames)
        ground_truth.append(
            ((start, len(probe_frames)), source, source_range, direction)
        )

    def edit(frame: bytes) -> bytes:
        # Crop a border, rescale to probe geometry, then apply a color grade.
        cropped = crop(
            frame,
            SOURCE_WIDTH,
            SOURCE_HEIGHT,
            x0=6,
            y0=4,
            crop_w=SOURCE_WIDTH - 12,
            crop_h=SOURCE_HEIGHT - 8,
        )
        resized = nearest_resize(
            cropped,
            SOURCE_WIDTH - 12,
            SOURCE_HEIGHT - 8,
            PROBE_WIDTH,
            PROBE_HEIGHT,
        )
        return color_grade(
            resized,
            gain=(1.18, 0.92, 0.85),
            offset=8,
        )

    # 1) Correct forward reuse from source-c (unique, textured).
    add(
        [edit(frame) for frame in c_frames[2:9]],
        "source-c",
        (2, 9),
        "forward",
    )

    # 2) Reversed reuse from source-a frames 10..5.
    reversed_segment = [edit(frame) for frame in reversed(a_frames[5:11])]
    add(reversed_segment, "source-a", (5, 11), "reversed")

    # 3) Repeated reuse of the same source-a segment (second occurrence).
    add(
        [edit(frame) for frame in a_frames[5:11]],
        "source-a",
        (5, 11),
        "forward",
    )

    # 4) Ambiguous: source-a/source-b near-duplicates, same interval.
    add(
        [edit(frame) for frame in b_frames[3:8]],
        "source-b",
        (3, 8),
        "forward",
    )

    # 5) Flat/static frames: must never confirm a unique source on their own.
    flat = [
        render_flat_frame(PROBE_WIDTH, PROBE_HEIGHT, (40, 40, 40))
        for _ in range(4)
    ]
    add(flat, "", (-1, -1), "static")

    # 6) Look-alike decoy content: the probe keeps the unshuffled original
    #    layout while the library only holds its shuffled twin. Retrieval hits
    #    on the shared coarse luma, but verification must reject the spatial
    #    mismatch, so no source is confirmed for this interval.
    add(
        [edit(frame) for frame in decoy_private[1:6]],
        "",
        (-1, -1),
        "decoy-rejected",
    )

    # 7) Genuinely new footage present in no source: stays unexplained.
    novel = [
        nearest_resize(
            render_stripe_frame(SOURCE_WIDTH, SOURCE_HEIGHT, t),
            SOURCE_WIDTH,
            SOURCE_HEIGHT,
            PROBE_WIDTH,
            PROBE_HEIGHT,
        )
        for t in range(5)
    ]
    add(novel, "", (-1, -1), "novel")

    probe_path = build_avi(
        root / "probe-edit.avi",
        probe_frames,
        PROBE_WIDTH,
        PROBE_HEIGHT,
        FPS,
    )
    return FixtureSet(
        source_paths=source_paths,
        probe_path=probe_path,
        ground_truth=ground_truth,
    )
