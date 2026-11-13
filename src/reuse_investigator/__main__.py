from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from reuse_investigator.config import InvestigatorSettings
from reuse_investigator.demo import build_fixture_set
from reuse_investigator.domain import ClipRole
from reuse_investigator.ingest import IngestService
from reuse_investigator.investigate import InvestigationService
from reuse_investigator.viewer import build_viewer
from reuse_investigator.workspace import Workspace


def _workspace(args: argparse.Namespace) -> tuple[Workspace, InvestigatorSettings]:
    settings = InvestigatorSettings.from_env()
    if args.data_dir:
        settings = InvestigatorSettings(
            data_root=Path(args.data_dir),
        )
    workspace = Workspace.open(settings.data_root)
    return workspace, settings


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _cmd_ingest(args: argparse.Namespace) -> int:
    workspace, settings = _workspace(args)
    service = IngestService(workspace, settings)
    role = ClipRole(args.role)
    manifest = service.register(
        args.clip_id,
        args.path,
        role,
        sample_stride=args.stride,
    )
    _print_json(
        {
            "registered": manifest.clip_id,
            "role": manifest.role,
            "frame_count": manifest.frame_count,
            "digest": manifest.digest,
            "profile": manifest.profile,
        }
    )
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    workspace, _settings = _workspace(args)
    payload = [
        manifest.as_dict()
        for manifest in workspace.list_manifests()
    ]
    _print_json(payload)
    return 0


def _cmd_refresh(args: argparse.Namespace) -> int:
    workspace, settings = _workspace(args)
    service = IngestService(workspace, settings)
    manifest = service.refresh(args.clip_id)
    _print_json(
        {
            "refreshed": manifest.clip_id,
            "signature_current": workspace.signature_is_current(manifest),
        }
    )
    return 0


def _cmd_investigate(args: argparse.Namespace) -> int:
    workspace, settings = _workspace(args)
    service = InvestigationService(workspace, settings)
    report = service.investigate(args.investigation_id, args.probe_clip_id)
    _print_json(
        {
            "investigation_id": report["investigation_id"],
            "status_counts": report["status_counts"],
            "findings": report["findings"],
            "unexplained": report["unexplained"],
        }
    )
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    workspace, _settings = _workspace(args)
    report = workspace.read_report(args.investigation_id)
    if report is None:
        print(f"no investigation named {args.investigation_id!r}")
        return 1
    _print_json(report)
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    fixture = build_fixture_set(args.output)
    _print_json(
        {
            "probe": str(fixture.probe_path),
            "sources": {
                key: str(path) for key, path in fixture.source_paths.items()
            },
            "ground_truth": [
                {
                    "probe_range": list(item[0]),
                    "source": item[1],
                    "source_range": list(item[2]),
                    "direction": item[3],
                }
                for item in fixture.ground_truth
            ],
        }
    )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    workspace, settings = _workspace(args)
    server = build_viewer(args.host, args.port, workspace, settings)
    host, port = server.server_address[:2]
    print(
        f"Reuse Investigator viewer listening on http://{host}:{port}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reuse-investigator")
    parser.add_argument(
        "--data-dir",
        help="investigation workspace root (default: REUSE_INVESTIGATOR_DATA_DIR)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="register a source or probe clip")
    ingest.add_argument("clip_id")
    ingest.add_argument("path")
    ingest.add_argument("--role", choices=["source", "probe"], required=True)
    ingest.add_argument("--stride", type=int, default=1)
    ingest.set_defaults(function=_cmd_ingest)

    list_cmd = sub.add_parser("list", help="list registered clips")
    list_cmd.set_defaults(function=_cmd_list)

    refresh = sub.add_parser(
        "refresh",
        help="re-validate a clip and resume/rebuild its signatures",
    )
    refresh.add_argument("clip_id")
    refresh.set_defaults(function=_cmd_refresh)

    investigate = sub.add_parser(
        "investigate",
        help="trace a probe clip against the current source library",
    )
    investigate.add_argument("investigation_id")
    investigate.add_argument("probe_clip_id")
    investigate.set_defaults(function=_cmd_investigate)

    report = sub.add_parser("report", help="print a stored investigation report")
    report.add_argument("investigation_id")
    report.set_defaults(function=_cmd_report)

    demo = sub.add_parser(
        "demo-footage",
        help="write the canonical re-edit demonstration fixtures",
    )
    demo.add_argument("--output", required=True)
    demo.set_defaults(function=_cmd_demo)

    serve = sub.add_parser("serve", help="start the interactive evidence viewer")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8871)
    serve.set_defaults(function=_cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.function(args))


if __name__ == "__main__":
    raise SystemExit(main())
