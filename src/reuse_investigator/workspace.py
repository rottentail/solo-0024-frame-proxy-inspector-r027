from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reuse_investigator.domain import (
    FrameSignature,
    MatchChain,
    MediaManifest,
    UnexplainedInterval,
)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def atomic_write_json(path: Path, value: Any) -> None:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    atomic_write_bytes(path, payload)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@dataclass(frozen=True, slots=True)
class Workspace:
    """On-disk layout for the standalone investigation workspace.

    Layout::

        <root>/
          manifests/<clip_id>.json
          signatures/<clip_id>/
             manifest.snapshot.json
             journal.ndjson        # one signature per line, append-only
             complete.flag
          evidence/<investigation_id>/
             report.json
             frames/<name>.ppm
    """

    root: Path

    @classmethod
    def open(cls, root: Path) -> "Workspace":
        workspace = cls(root=root.expanduser().resolve())
        for directory in (
            workspace.root,
            workspace.manifests,
            workspace.signatures,
            workspace.evidence,
            workspace.locks,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return workspace

    @property
    def manifests(self) -> Path:
        return self.root / "manifests"

    @property
    def signatures(self) -> Path:
        return self.root / "signatures"

    @property
    def evidence(self) -> Path:
        return self.root / "evidence"

    @property
    def locks(self) -> Path:
        return self.root / "locks"

    def manifest_path(self, clip_id: str) -> Path:
        return self.manifests / f"{clip_id}.json"

    def signature_dir(self, clip_id: str) -> Path:
        return self.signatures / clip_id

    def journal_path(self, clip_id: str) -> Path:
        return self.signature_dir(clip_id) / "journal.ndjson"

    def snapshot_path(self, clip_id: str) -> Path:
        return self.signature_dir(clip_id) / "manifest.snapshot.json"

    def complete_flag(self, clip_id: str) -> Path:
        return self.signature_dir(clip_id) / "complete.flag"

    def investigation_dir(self, investigation_id: str) -> Path:
        return self.evidence / investigation_id

    def report_path(self, investigation_id: str) -> Path:
        return self.investigation_dir(investigation_id) / "report.json"

    def frame_evidence_dir(self, investigation_id: str) -> Path:
        return self.investigation_dir(investigation_id) / "frames"

    # ---- manifest -------------------------------------------------------

    def write_manifest(self, manifest: MediaManifest) -> None:
        atomic_write_json(self.manifest_path(manifest.clip_id), manifest.as_dict())

    def read_manifest(self, clip_id: str) -> MediaManifest | None:
        path = self.manifest_path(clip_id)
        if not path.exists():
            return None
        return MediaManifest.from_dict(read_json(path))

    def list_manifests(self, role: str | None = None) -> list[MediaManifest]:
        items: list[MediaManifest] = []
        for path in sorted(self.manifests.glob("*.json")):
            manifest = MediaManifest.from_dict(read_json(path))
            if role is None or manifest.role == role:
                items.append(manifest)
        return items

    # ---- signature journal ---------------------------------------------

    def signature_is_current(self, manifest: MediaManifest) -> bool:
        """Evidence is reusable only when content and conditions still match.

        A signature set is considered current only if the completion flag is
        present, the stored analysis snapshot still equals the manifest's
        analysis key, and the journal holds exactly the declared frames.
        """
        if not self.complete_flag(manifest.clip_id).exists():
            return False
        snapshot_path = self.snapshot_path(manifest.clip_id)
        if not snapshot_path.exists():
            return False
        snapshot = read_json(snapshot_path)
        # The analysis key is a flat scalar tuple; JSON stores it as a list.
        # Compare element-wise because tuple/list equality is always False.
        return list(snapshot.get("analysis_key", [])) == list(
            manifest.analysis_key()
        ) and self.count_journal_entries(
            manifest.clip_id
        ) == manifest.frame_count

    def count_journal_entries(self, clip_id: str) -> int:
        path = self.journal_path(clip_id)
        if not path.exists():
            return 0
        count = 0
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    count += 1
        return count

    def reset_signatures(self, clip_id: str) -> None:
        directory = self.signature_dir(clip_id)
        if directory.exists():
            for child in directory.iterdir():
                child.unlink()
        directory.mkdir(parents=True, exist_ok=True)

    def begin_signatures(self, manifest: MediaManifest) -> None:
        self.reset_signatures(manifest.clip_id)
        atomic_write_json(
            self.snapshot_path(manifest.clip_id),
            {"analysis_key": list(manifest.analysis_key())},
        )

    def append_signature(
        self,
        clip_id: str,
        signature: FrameSignature,
    ) -> None:
        path = self.journal_path(clip_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(signature.as_dict(), sort_keys=True))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def complete_signatures(self, clip_id: str) -> None:
        atomic_write_bytes(self.complete_flag(clip_id), b"complete\n")

    def load_signatures(self, clip_id: str) -> list[FrameSignature]:
        path = self.journal_path(clip_id)
        signatures: list[FrameSignature] = []
        if not path.exists():
            return signatures
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    signatures.append(
                        FrameSignature.from_dict(json.loads(line))
                    )
        signatures.sort(key=lambda item: item.frame_index)
        return signatures

    # ---- investigation report ------------------------------------------

    def write_report(
        self,
        investigation_id: str,
        report: dict[str, Any],
    ) -> None:
        atomic_write_json(self.report_path(investigation_id), report)

    def read_report(self, investigation_id: str) -> dict[str, Any] | None:
        path = self.report_path(investigation_id)
        if not path.exists():
            return None
        return read_json(path)

    def list_reports(self) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        for path in sorted(self.evidence.glob("*/report.json")):
            reports.append(read_json(path))
        return reports

    def write_frame_evidence(
        self,
        investigation_id: str,
        name: str,
        payload: bytes,
    ) -> Path:
        directory = self.frame_evidence_dir(investigation_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name
        atomic_write_bytes(target, payload)
        return target

    def read_frame_evidence(self, investigation_id: str, name: str) -> bytes:
        return (self.frame_evidence_dir(investigation_id) / name).read_bytes()

    def frame_evidence_exists(self, investigation_id: str, name: str) -> bool:
        return (self.frame_evidence_dir(investigation_id) / name).exists()


def chains_to_dicts(chains: list[MatchChain]) -> list[dict[str, Any]]:
    return [chain.as_dict() for chain in chains]


def intervals_to_dicts(
    intervals: list[UnexplainedInterval],
) -> list[dict[str, Any]]:
    return [interval.as_dict() for interval in intervals]
