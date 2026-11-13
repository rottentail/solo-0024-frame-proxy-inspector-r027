from __future__ import annotations

from pathlib import Path
from typing import Any

from reuse_investigator.config import InvestigatorSettings
from reuse_investigator.domain import (
    FrameSignature,
    MatchChain,
    MediaManifest,
    UnexplainedInterval,
)
from reuse_investigator.evidence import render_pair_ppm
from reuse_investigator.matching import MatchingEngine, SourceLibrary
from reuse_investigator.media_adapter import MediaHandle, decode_one, open_media
from reuse_investigator.workspace import Workspace


class InvestigationService:
    """Runs a reuse investigation and renders navigable frame evidence.

    Only compact signatures are held in memory for matching. When evidence is
    rendered the two frames involved are decoded on demand, two frames at a
    time, and then released; decoded pixels for the whole library are never
    resident simultaneously.
    """

    def __init__(
        self,
        workspace: Workspace,
        settings: InvestigatorSettings,
    ) -> None:
        self._workspace = workspace
        self._settings = settings
        self._engine = MatchingEngine(settings)

    def investigate(
        self,
        investigation_id: str,
        probe_clip_id: str,
    ) -> dict[str, Any]:
        probe_manifest = self._require_manifest(probe_clip_id, "probe")
        source_manifests = [
            manifest
            for manifest in self._workspace.list_manifests(role="source")
            if self._workspace.signature_is_current(manifest)
        ]
        if not source_manifests:
            raise ValueError(
                "no current source signatures are available; ingest sources first"
            )

        library = self._load_library(source_manifests)
        probe_signatures = self._workspace.load_signatures(probe_clip_id)
        if not self._workspace.signature_is_current(probe_manifest):
            raise ValueError(
                f"probe {probe_clip_id!r} signatures are missing or stale; "
                "re-ingest before investigating"
            )

        chains, intervals, outcomes = self._engine.investigate(
            probe_signatures,
            library,
        )
        evidence_map = self._render_evidence(
            investigation_id,
            probe_manifest,
            source_manifests,
            chains,
        )

        report = self._build_report(
            investigation_id,
            probe_manifest,
            source_manifests,
            chains,
            intervals,
            evidence_map,
            outcomes,
        )
        self._workspace.write_report(investigation_id, report)
        return report

    def _require_manifest(
        self,
        clip_id: str,
        expected_role: str,
    ) -> MediaManifest:
        manifest = self._workspace.read_manifest(clip_id)
        if manifest is None:
            raise KeyError(f"no registered clip named {clip_id!r}")
        if manifest.role != expected_role:
            raise ValueError(
                f"clip {clip_id!r} has role {manifest.role!r}, "
                f"expected {expected_role!r}"
            )
        return manifest

    def _load_library(
        self,
        manifests: list[MediaManifest],
    ) -> SourceLibrary:
        signatures: dict[str, list[FrameSignature]] = {}
        for manifest in manifests:
            signatures[manifest.clip_id] = self._workspace.load_signatures(
                manifest.clip_id
            )
        return SourceLibrary(
            clip_ids=tuple(manifest.clip_id for manifest in manifests),
            signatures=signatures,
        )

    def _render_evidence(
        self,
        investigation_id: str,
        probe_manifest: MediaManifest,
        source_manifests: list[MediaManifest],
        chains: list[MatchChain],
    ) -> dict[str, dict[str, Any]]:
        source_by_id = {
            manifest.clip_id: manifest for manifest in source_manifests
        }
        evidence: dict[str, dict[str, Any]] = {}
        probe_handle: MediaHandle | None = None
        source_handles: dict[str, MediaHandle] = {}

        try:
            probe_handle = open_media(probe_manifest.path)
            for chain in chains:
                source_manifest = source_by_id[chain.source_clip_id]
                source_handles.setdefault(
                    chain.source_clip_id,
                    open_media(source_manifest.path),
                )
                probe_frame_index = (
                    chain.probe_start + chain.probe_end
                ) // 2
                source_frame_index = self._source_midpoint(chain)
                name = (
                    f"probe-{probe_frame_index:04d}__"
                    f"{chain.source_clip_id}-{source_frame_index:04d}.ppm"
                )
                if not self._workspace.frame_evidence_exists(
                    investigation_id,
                    name,
                ):
                    # Exactly two decoded frames are resident here.
                    probe_frame = decode_one(
                        probe_handle,
                        probe_frame_index,
                    )
                    source_frame = decode_one(
                        source_handles[chain.source_clip_id],
                        source_frame_index,
                    )
                    payload = render_pair_ppm(
                        probe_frame,
                        source_frame,
                        probe_label=f"{probe_manifest.clip_id}#{probe_frame_index}",
                        source_label=(
                            f"{chain.source_clip_id}#{source_frame_index}"
                        ),
                    )
                    self._workspace.write_frame_evidence(
                        investigation_id,
                        name,
                        payload,
                    )
                evidence[self._chain_key(chain)] = {
                    "artifact": f"frames/{name}",
                    "probe_frame": probe_frame_index,
                    "source_clip_id": chain.source_clip_id,
                    "source_frame": source_frame_index,
                    "probe_media": probe_manifest.path,
                    "source_media": source_manifest.path,
                }
        finally:
            # Handles hold mmap resources only during rendering; dropping them
            # closes the maps. Decoded frames are local and reclaimed at once.
            del probe_handle
            source_handles.clear()
        return evidence

    @staticmethod
    def _source_midpoint(chain: MatchChain) -> int:
        if chain.direction == "reversed":
            # source_start > source_end for reversed chains; the midpoint still
            # points at a real frame inside the covered source interval.
            low = chain.source_end + 1
            high = chain.source_start
        else:
            low = chain.source_start
            high = chain.source_end - 1
        return (low + high) // 2

    @staticmethod
    def _chain_key(chain: MatchChain) -> str:
        return f"{chain.source_clip_id}:{chain.probe_start}-{chain.probe_end}"

    def _build_report(
        self,
        investigation_id: str,
        probe_manifest: MediaManifest,
        source_manifests: list[MediaManifest],
        chains: list[MatchChain],
        intervals: list[UnexplainedInterval],
        evidence_map: dict[str, dict[str, Any]],
        outcomes: dict[str, Any],
    ) -> dict[str, Any]:
        chain_payload: list[dict[str, Any]] = []
        for chain in chains:
            entry = chain.as_dict()
            entry["evidence"] = evidence_map.get(
                self._chain_key(chain)
            )
            chain_payload.append(entry)
        chain_payload.sort(key=lambda item: item["probe_range"][0])

        status_counts: dict[str, int] = {}
        for chain in chain_payload:
            status_counts[chain["status"]] = (
                status_counts.get(chain["status"], 0) + 1
            )

        return {
            "investigation_id": investigation_id,
            "profile": probe_manifest.profile,
            "probe": {
                "clip_id": probe_manifest.clip_id,
                "digest": probe_manifest.digest,
                "path": probe_manifest.path,
                "width": probe_manifest.width,
                "height": probe_manifest.height,
                "frame_count": probe_manifest.frame_count,
            },
            "sources": [
                {
                    "clip_id": manifest.clip_id,
                    "digest": manifest.digest,
                    "path": manifest.path,
                    "frame_count": manifest.frame_count,
                }
                for manifest in source_manifests
            ],
            "analysis_conditions": {
                "grid_size": self._settings.grid_size,
                "retrieval_mad_gate": self._settings.retrieval_mad_gate,
                "retrieval_hamming_radius": (
                    self._settings.retrieval_hamming_radius
                ),
                "retrieval_candidate_cap": (
                    self._settings.retrieval_candidate_cap
                ),
                "verify_ncc_strong": self._settings.verify_ncc_strong,
                "verify_ncc_weak": self._settings.verify_ncc_weak,
                "verify_color_gate": self._settings.verify_color_gate,
                "flat_variance_floor": self._settings.flat_variance_floor,
                "repeated_frame_mad": self._settings.repeated_frame_mad,
                "uniqueness_margin": self._settings.uniqueness_margin,
                "min_chain_frames": self._settings.min_chain_frames,
                "note": (
                    "retrieval hits are candidates only; confirmation requires "
                    "NCC, color, temporal continuity, and a uniqueness margin"
                ),
            },
            "status_counts": status_counts,
            "findings": chain_payload,
            "unexplained": [interval.as_dict() for interval in intervals],
            "frame_outcomes": [
                {
                    "probe_frame": outcome.probe_frame,
                    "candidate_count": outcome.candidate_count,
                    "passed_verification": outcome.passed_count,
                    "repeated_held_frame": outcome.repeated,
                    "best_ncc": outcome.best_ncc,
                    "best_candidate": (
                        None
                        if outcome.best_clip_id is None
                        else {
                            "source_clip_id": outcome.best_clip_id,
                            "source_frame": outcome.best_source_frame,
                        }
                    ),
                    "disposition": (
                        "repeated_held_frame"
                        if outcome.repeated
                        else "rejected_candidate"
                        if outcome.rejected_candidate
                        else "no_candidate"
                        if outcome.no_candidate
                        else "verified"
                    ),
                }
                for outcome in (outcomes.values() if isinstance(outcomes, dict) else ())
            ],
        }

    def get_report(self, investigation_id: str) -> dict[str, Any]:
        report = self._workspace.read_report(investigation_id)
        if report is None:
            raise KeyError(f"no investigation named {investigation_id!r}")
        return report

    def get_evidence_bytes(
        self,
        investigation_id: str,
        name: str,
    ) -> bytes:
        # Guard against path traversal outside the investigation directory.
        safe_name = Path(name).name
        return self._workspace.read_frame_evidence(
            investigation_id,
            safe_name,
        )
