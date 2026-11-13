from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ClipRole(str, Enum):
    SOURCE = "source"
    PROBE = "probe"


class Direction(str, Enum):
    FORWARD = "forward"
    REVERSED = "reversed"


class FindingStatus(str, Enum):
    """Determination states for an investigated probe interval."""

    # Verification gates and the uniqueness margin were satisfied.
    CONFIRMED = "confirmed"
    # Verification passed but the source could not be uniquely distinguished
    # (near-duplicate sources, flat footage, or a tied runner-up).
    AMBIGUOUS = "ambiguous"
    # Retrieval produced candidates but verification rejected every one.
    REJECTED = "rejected"
    # No candidate survived even the retrieval stage.
    UNEXPLAINED = "unexplained"


@dataclass(frozen=True, slots=True)
class MediaManifest:
    """Content identity and the analysis conditions the evidence depends on.

    Every derived signature and every finding references this manifest. The
    evidence is reusable only while the digest, geometry, and analysis profile
    all still agree with the material on disk.
    """

    clip_id: str
    role: str
    path: str
    digest: str
    byte_size: int
    width: int
    height: int
    frame_count: int
    fps_numerator: int
    fps_denominator: int
    profile: str
    grid_size: int
    sample_stride: int
    created_at: str = field(default_factory=utc_now)

    def analysis_key(self) -> tuple[Any, ...]:
        return (
            self.digest,
            self.width,
            self.height,
            self.frame_count,
            self.profile,
            self.grid_size,
            self.sample_stride,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MediaManifest":
        return cls(
            clip_id=str(data["clip_id"]),
            role=str(data["role"]),
            path=str(data["path"]),
            digest=str(data["digest"]),
            byte_size=int(data["byte_size"]),
            width=int(data["width"]),
            height=int(data["height"]),
            frame_count=int(data["frame_count"]),
            fps_numerator=int(data["fps_numerator"]),
            fps_denominator=int(data["fps_denominator"]),
            profile=str(data["profile"]),
            grid_size=int(data["grid_size"]),
            sample_stride=int(data["sample_stride"]),
            created_at=str(data.get("created_at", utc_now())),
        )


@dataclass(frozen=True, slots=True)
class FrameSignature:
    """A compact, transform-tolerant frame descriptor.

    ``grid`` holds area-averaged luma cells (0..255). ``bits`` is the binary
    thresholded form used purely for coarse Hamming retrieval. The color
    means are scale/offset tolerant because verification compares them after
    per-frame normalization.
    """

    frame_index: int
    grid: tuple[int, ...]
    bits: int
    mean_red: float
    mean_green: float
    mean_blue: float
    grid_variance: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "frame_index": self.frame_index,
            "grid": list(self.grid),
            "bits": self.bits,
            "mean_red": round(self.mean_red, 4),
            "mean_green": round(self.mean_green, 4),
            "mean_blue": round(self.mean_blue, 4),
            "grid_variance": round(self.grid_variance, 4),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FrameSignature":
        return cls(
            frame_index=int(data["frame_index"]),
            grid=tuple(int(value) for value in data["grid"]),
            bits=int(data["bits"]),
            mean_red=float(data["mean_red"]),
            mean_green=float(data["mean_green"]),
            mean_blue=float(data["mean_blue"]),
            grid_variance=float(data["grid_variance"]),
        )


@dataclass(frozen=True, slots=True)
class FrameCandidate:
    """A retrieval hit. This is *not* a verified source."""

    source_clip_id: str
    source_frame: int
    hamming: int
    grid_mad: float
    color_distance: float


@dataclass(frozen=True, slots=True)
class FrameVerification:
    """The result of actually verifying a single frame pair."""

    source_clip_id: str
    probe_frame: int
    source_frame: int
    ncc: float
    color_distance: float
    hamming: int
    flat: bool
    passed: bool
    stage: str


@dataclass(frozen=True, slots=True)
class MatchChain:
    """A temporally coherent correspondence over a contiguous probe interval."""

    source_clip_id: str
    probe_start: int
    probe_end: int  # half-open
    source_start: int
    source_end: int  # half-open
    direction: str
    mean_ncc: float
    mean_color_distance: float
    flat_frames: int
    verified_frames: int
    status: str
    score: float
    alternatives: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_clip_id": self.source_clip_id,
            "probe_range": [self.probe_start, self.probe_end],
            "source_range": [self.source_start, self.source_end],
            "direction": self.direction,
            "mean_ncc": round(self.mean_ncc, 4),
            "mean_color_distance": round(self.mean_color_distance, 4),
            "flat_frames": self.flat_frames,
            "verified_frames": self.verified_frames,
            "status": self.status,
            "score": round(self.score, 4),
            "alternatives": [dict(item) for item in self.alternatives],
        }


@dataclass(frozen=True, slots=True)
class UnexplainedInterval:
    probe_start: int
    probe_end: int  # half-open
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "probe_range": [self.probe_start, self.probe_end],
            "reason": self.reason,
        }


def digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
