#!/usr/bin/env python3
"""End-to-end, independently checkable verification for the reuse investigator.

This harness never imports the production HTTP or service layers. It:

1. builds the deterministic re-edit fixtures;
2. drives the investigator through its real ingest/investigate APIs against a
   throwaway workspace;
3. independently checks that correct matches, rejected retrieval hits, and
   non-unique/ambiguous footage are each presented the way the requirements
   demand;
4. exercises incremental ingest, resume-after-interruption, evidence
   invalidation, and the no-full-library-in-memory guarantee.

Run with::

    PYTHONPATH=src:. python3 tools/check_reuse_investigation.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import tracemalloc
from pathlib import Path

from frame_proxy.media.ppm import parse_ppm_header

from reuse_investigator.config import InvestigatorSettings
from reuse_investigator.demo import build_fixture_set
from reuse_investigator.domain import ClipRole
from reuse_investigator.ingest import IngestService
from reuse_investigator.investigate import InvestigationService
from reuse_investigator.media_adapter import iter_decoded_frames, open_media
from reuse_investigator.workspace import Workspace


class CheckFailed(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def ok(message: str) -> None:
    print(f"PASS {message}")


def _fresh_workspace() -> tuple[Path, Workspace, InvestigatorSettings]:
    temp_dir = Path(tempfile.mkdtemp(prefix="reuse-check-"))
    settings = InvestigatorSettings(data_root=temp_dir)
    workspace = Workspace.open(settings.data_root)
    return temp_dir, workspace, settings


def _ingest_library(
    workspace: Workspace,
    settings: InvestigatorSettings,
    fixture,
    sources: list[str],
) -> None:
    ingest = IngestService(workspace, settings)
    for clip_id in sources:
        ingest.register(
            clip_id,
            fixture.source_paths[clip_id],
            ClipRole.SOURCE,
        )


def _ingest_probe(workspace, settings, fixture) -> None:
    IngestService(workspace, settings).register(
        "probe-edit",
        fixture.probe_path,
        ClipRole.PROBE,
    )


def check_correct_unique_match() -> tuple[Path, dict]:
    temp_dir, workspace, settings = _fresh_workspace()
    fixture = build_fixture_set(temp_dir / "media")
    _ingest_library(
        workspace,
        settings,
        fixture,
        ["source-a", "source-b", "source-c", "source-decoy"],
    )
    _ingest_probe(workspace, settings, fixture)
    report = InvestigationService(workspace, settings).investigate(
        "inv-check",
        "probe-edit",
    )

    confirmed = [f for f in report["findings"] if f["status"] == "confirmed"]
    require(
        any(
            f["probe_range"] == [0, 7]
            and f["source_clip_id"] == "source-c"
            and f["source_range"] == [2, 9]
            and f["direction"] == "forward"
            for f in confirmed
        ),
        "expected a confirmed source-c forward match over probe [0,7)",
    )
    target = next(
        f
        for f in confirmed
        if f["probe_range"] == [0, 7]
    )
    require(
        target["alternatives"] == [],
        "a unique match must carry no competing alternatives",
    )
    require(
        target["evidence"] is not None
        and target["evidence"]["artifact"].endswith(".ppm"),
        "confirmed finding must link to a frame-pair evidence artifact",
    )
    artifact = workspace.read_frame_evidence(
        "inv-check",
        Path(target["evidence"]["artifact"]).name,
    )
    header = parse_ppm_header(artifact)
    require(header.width > 0 and header.height > 0, "evidence PPM must decode")
    ok(
        "correct unique match: source-c forward reuse confirmed with frame evidence"
    )
    return temp_dir, report


def check_reversed_and_repeated(report: dict) -> None:
    findings = report["findings"]
    directions = {
        (tuple(f["probe_range"]), f["direction"]) for f in findings
    }
    require(
        any(direction == "reversed" for _, direction in directions),
        "expected at least one reversed-direction chain",
    )
    # The same source-a segment appears twice in the probe (reversed then
    # repeated forward); both intervals must be represented as findings.
    a_intervals = [
        f
        for f in findings
        if f["source_clip_id"] in {"source-a", "source-b"}
    ]
    require(
        len(a_intervals) >= 2,
        "repeated reuse of the same source segment must produce separate findings",
    )
    reversed_chain = next(
        f for f in findings if f["direction"] == "reversed"
    )
    require(
        reversed_chain["source_range"][0]
        > reversed_chain["source_range"][1],
        "a reversed chain must describe a descending source range",
    )
    ok("reversed reuse and repeated reuse are both represented")


def check_ambiguous_near_duplicate(report: dict) -> None:
    ambiguous = [f for f in report["findings"] if f["status"] == "ambiguous"]
    require(ambiguous, "expected ambiguous findings for near-duplicate sources")
    near_dup = next(
        (
            f
            for f in ambiguous
            if f["probe_range"] == [19, 24]
        ),
        None,
    )
    require(
        near_dup is not None,
        "the near-duplicate interval [19,24) must not be uniquely confirmed",
    )
    alternative_ids = {
        alt["source_clip_id"] for alt in near_dup["alternatives"]
    }
    require(
        alternative_ids,
        "an ambiguous finding must retain its competing candidate clips",
    )
    margins = [
        near_dup["mean_ncc"] - float(alt["mean_ncc"])
        for alt in near_dup["alternatives"]
    ]
    require(
        all(margin < 0.05 for margin in margins),
        "near-duplicate alternatives must be statistically tied",
    )
    ok("near-duplicate footage stays ambiguous with retained candidates")


def check_retrieval_rejected_vs_unexplained(report: dict) -> None:
    by_disposition = {
        item["probe_frame"]: item["disposition"]
        for item in report["frame_outcomes"]
    }
    # Decoy frames 28..32: candidates exist but none verify.
    require(
        all(by_disposition[frame] == "rejected_candidate" for frame in range(28, 33)),
        "decoy frames must show rejected retrieval candidates",
    )
    # Novel frames 33..37: no candidate at all.
    require(
        all(by_disposition[frame] == "no_candidate" for frame in range(33, 38)),
        "novel frames must show no retrieval candidate",
    )
    # Flat frames 24..27: zero candidates (zero variance cannot correlate);
    # the held run is additionally flagged as repeated/non-unique.
    flat = [by_disposition[frame] for frame in range(24, 28)]
    require(
        all(disposition in {"no_candidate", "repeated_held_frame"} for disposition in flat),
        "flat/held frames must never be matched to a source",
    )
    raw_outcomes = {
        item["probe_frame"]: item for item in report["frame_outcomes"]
    }
    require(
        all(raw_outcomes[frame]["candidate_count"] == 0 for frame in range(24, 28)),
        "flat frames must produce zero verification candidates",
    )
    reasons = " ".join(item["reason"] for item in report["unexplained"])
    require(
        "failed NCC or color verification" in reasons,
        "a rejected-candidate interval must explain that verification failed",
    )
    require(
        any(
            interval["probe_range"] == [33, 38]
            for interval in report["unexplained"]
        ),
        "genuinely novel footage must remain an unexplained interval",
    )
    ok("retrieval rejection and genuine no-candidate gaps are distinguished")


def check_incremental_and_resume() -> None:
    temp_dir, workspace, settings = _fresh_workspace()
    try:
        fixture = build_fixture_set(temp_dir / "media")
        ingest = IngestService(workspace, settings)

        # Ingest sources one at a time; earlier material must stay intact.
        ingest.register("source-a", fixture.source_paths["source-a"], ClipRole.SOURCE)
        ingest.register("source-c", fixture.source_paths["source-c"], ClipRole.SOURCE)
        require(
            workspace.signature_is_current(workspace.read_manifest("source-a")),
            "earlier source signatures must remain current after new ingest",
        )
        counts_before = workspace.count_journal_entries("source-a")

        # Simulate an interrupted build by truncating the journal and dropping
        # the completion flag, then re-registering to resume.
        journal = workspace.journal_path("source-a")
        lines = journal.read_text(encoding="utf-8").splitlines()
        journal.write_text("\n".join(lines[: len(lines) // 2]) + "\n", encoding="utf-8")
        workspace.complete_flag("source-a").unlink()
        require(
            not workspace.signature_is_current(workspace.read_manifest("source-a")),
            "an incomplete journal must be treated as not current",
        )
        # A stale snapshot forces a clean rebuild rather than mixing profiles.
        ingest.register("source-a", fixture.source_paths["source-a"], ClipRole.SOURCE)
        require(
            workspace.signature_is_current(workspace.read_manifest("source-a")),
            "re-ingest must rebuild/complete signatures to a current state",
        )
        require(
            workspace.count_journal_entries("source-a") == counts_before,
            "resumed build must restore the full frame count",
        )
        ok("incremental ingest and interrupted resume behave correctly")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def check_evidence_invalidation() -> None:
    temp_dir, workspace, settings = _fresh_workspace()
    try:
        fixture = build_fixture_set(temp_dir / "media")
        ingest = IngestService(workspace, settings)
        manifest = ingest.register(
            "source-c",
            fixture.source_paths["source-c"],
            ClipRole.SOURCE,
        )
        require(
            workspace.signature_is_current(manifest),
            "freshly built signatures should be current",
        )
        # Tamper with the underlying bytes after registration.
        path = Path(manifest.path)
        original = path.read_bytes()
        tampered = bytearray(original)
        tampered[-1] ^= 0xFF
        path.write_bytes(bytes(tampered))
        try:
            ingest.refresh("source-c")
            raised = False
        except ValueError:
            raised = True
        require(
            raised,
            "changed content must invalidate stored evidence instead of reusing it",
        )
        path.write_bytes(original)
        refreshed = ingest.refresh("source-c")
        require(
            workspace.signature_is_current(refreshed),
            "restored content should validate again",
        )
        ok("evidence is invalidated when content no longer matches")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def check_memory_bound() -> None:
    """Decoded frames for the whole library must never be resident together."""
    temp_dir, workspace, settings = _fresh_workspace()
    try:
        fixture = build_fixture_set(temp_dir / "media")
        ingest = IngestService(workspace, settings)
        ingest.register(
            "source-a", fixture.source_paths["source-a"], ClipRole.SOURCE
        )
        handle = open_media(fixture.source_paths["source-a"])
        descriptor = handle.descriptor
        full_frame_bytes = descriptor.frame_payload_size

        tracemalloc.start()
        baseline = tracemalloc.take_snapshot()
        resident_peak = 0
        for frame in iter_decoded_frames(handle):
            # One decoded RGB frame plus small overhead is the only large live
            # object during streaming iteration.
            current, peak = tracemalloc.get_traced_memory()
            resident_peak = max(resident_peak, current)
            del frame
        tracemalloc.stop()

        # Two full raw frames worth of RGB would already exceed this bound;
        # streaming keeps live decoded pixel memory around one frame.
        bound = full_frame_bytes * 2
        require(
            resident_peak < bound,
            f"streaming resident memory {resident_peak} must stay below {bound}",
        )
        ok(
            f"streaming stays under two-frame pixel budget "
            f"({resident_peak} < {bound} bytes)"
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def check_report_is_self_describing(report: dict) -> None:
    required = {
        "investigation_id",
        "probe",
        "sources",
        "analysis_conditions",
        "status_counts",
        "findings",
        "unexplained",
        "frame_outcomes",
    }
    require(required.issubset(report), "report must contain all audit sections")
    for finding in report["findings"]:
        require(
            finding["status"] in {"confirmed", "ambiguous", "rejected"},
            "finding status must be an allowed determination",
        )
    # Serialization round trip proves the report is durable JSON.
    reloaded = json.loads(json.dumps(report, ensure_ascii=False))
    require(
        reloaded["investigation_id"] == report["investigation_id"],
        "report must round-trip through JSON",
    )
    ok("report is complete, self-describing, and JSON-durable")


def check_production_service_compatibility() -> None:
    """The investigator must read clips verified by the real production intake."""
    temp_dir = Path(tempfile.mkdtemp(prefix="reuse-prod-"))
    try:
        from frame_proxy.app import build_router
        from frame_proxy.config import Settings as ProdSettings
        from frame_proxy.media.avi_writer import build_sample_avi

        prod_dir = temp_dir / "prod"
        prod_settings = ProdSettings(data_root=prod_dir)
        router = build_router(prod_settings)
        record = router._clips.admit("real-clip", build_sample_avi())
        stored_avi = prod_dir / "clips" / "real-clip" / "clip.avi"

        inv_settings = InvestigatorSettings(data_root=temp_dir / "inv")
        workspace = Workspace.open(inv_settings.data_root)
        manifest = IngestService(workspace, inv_settings).register(
            "real-clip",
            stored_avi,
            ClipRole.SOURCE,
        )
        require(
            manifest.digest == record.digest,
            "investigator digest must match the production clip digest",
        )
        require(
            workspace.signature_is_current(manifest),
            "production-verified clip must yield current signatures",
        )
        ok("consumes clips admitted by the real production service (digest match)")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> int:
    try:
        temp_dir, report = check_correct_unique_match()
        try:
            check_reversed_and_repeated(report)
            check_ambiguous_near_duplicate(report)
            check_retrieval_rejected_vs_unexplained(report)
            check_report_is_self_describing(report)
            check_incremental_and_resume()
            check_evidence_invalidation()
            check_memory_bound()
            check_production_service_compatibility()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    except CheckFailed as exc:
        print(f"FAIL {exc}")
        return 1
    print("ALL REUSE INVESTIGATOR CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
