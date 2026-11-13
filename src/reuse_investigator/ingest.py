from __future__ import annotations

from pathlib import Path

from reuse_investigator import SIGNATURE_PROFILE
from reuse_investigator.config import InvestigatorSettings
from reuse_investigator.domain import ClipRole, MediaManifest
from reuse_investigator.media_adapter import (
    MediaHandle,
    iter_decoded_frames,
    open_media,
)
from reuse_investigator.signature import signature_for_frame
from reuse_investigator.workspace import Workspace


class IngestService:
    """Registers material and builds resumable per-frame signatures.

    Ingest is incremental and restartable:

    * new clips are processed one at a time and never disturb existing ones;
    * each frame signature is appended to a durable journal and fsynced, so an
      interrupted run resumes at the first missing frame;
    * already-built signatures are reused only while the stored content digest
      and the full analysis conditions still match the material on disk.
    """

    def __init__(
        self,
        workspace: Workspace,
        settings: InvestigatorSettings,
    ) -> None:
        self._workspace = workspace
        self._settings = settings

    def register(
        self,
        clip_id: str,
        path: str | Path,
        role: ClipRole,
        *,
        sample_stride: int = 1,
    ) -> MediaManifest:
        handle = open_media(path)
        self._guard_geometry(handle)
        descriptor = handle.descriptor
        manifest = MediaManifest(
            clip_id=clip_id,
            role=role.value,
            path=str(Path(path).expanduser().resolve()),
            digest=handle.digest,
            byte_size=handle.byte_size,
            width=descriptor.width,
            height=descriptor.height,
            frame_count=descriptor.frame_count,
            fps_numerator=descriptor.fps_numerator,
            fps_denominator=descriptor.fps_denominator,
            profile=SIGNATURE_PROFILE,
            grid_size=self._settings.grid_size,
            sample_stride=sample_stride,
        )
        self._workspace.write_manifest(manifest)
        self._build_signatures(handle, manifest)
        return manifest

    def _guard_geometry(self, handle: MediaHandle) -> None:
        descriptor = handle.descriptor
        if descriptor.width > self._settings.max_decode_width:
            raise ValueError("clip width exceeds the investigator decode limit")
        if descriptor.height > self._settings.max_decode_height:
            raise ValueError("clip height exceeds the investigator decode limit")
        if descriptor.frame_count > self._settings.max_frame_count:
            raise ValueError("clip frame count exceeds the investigator limit")

    def _build_signatures(
        self,
        handle: MediaHandle,
        manifest: MediaManifest,
    ) -> None:
        # Reuse only when the evidence conditions still match. Any mismatch in
        # content or analysis profile invalidates the derived signatures.
        if self._workspace.signature_is_current(manifest):
            return

        existing = self._workspace.count_journal_entries(manifest.clip_id)
        snapshot_current = self._snapshot_matches(manifest)
        if existing == 0 or not snapshot_current:
            # Either no work exists yet, or the journal was produced under a
            # different content/analysis profile. Start clean rather than mix
            # incompatible descriptors.
            self._workspace.begin_signatures(manifest)
            existing = 0

        # Streaming: one decoded frame is resident at any moment. On resume we
        # still walk frames in order but skip the already-journaled prefix.
        for index, frame in enumerate(iter_decoded_frames(handle)):
            if index < existing:
                continue
            signature = signature_for_frame(
                frame,
                frame.index,
                self._settings,
            )
            self._workspace.append_signature(manifest.clip_id, signature)

        if (
            self._workspace.count_journal_entries(manifest.clip_id)
            != manifest.frame_count
        ):
            raise RuntimeError(
                "signature journal does not cover every frame; "
                "the next run will resume from the journal"
            )
        self._workspace.complete_signatures(manifest.clip_id)

    def _snapshot_matches(self, manifest: MediaManifest) -> bool:
        snapshot_path = self._workspace.snapshot_path(manifest.clip_id)
        if not snapshot_path.exists():
            return False
        from reuse_investigator.workspace import read_json

        snapshot = read_json(snapshot_path)
        return list(snapshot.get("analysis_key", [])) == list(
            manifest.analysis_key()
        )

    def refresh(self, clip_id: str) -> MediaManifest:
        """Re-validate stored material against the file it was registered from."""
        manifest = self._workspace.read_manifest(clip_id)
        if manifest is None:
            raise KeyError(f"no registered clip named {clip_id!r}")
        current = open_media(manifest.path)
        if current.digest != manifest.digest:
            raise ValueError(
                f"content of {clip_id!r} changed on disk; stored evidence is "
                "no longer valid and must be re-ingested"
            )
        # Rebuild if analysis conditions drifted even though bytes are intact.
        self._build_signatures(current, manifest)
        return manifest
