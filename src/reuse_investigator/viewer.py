from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from reuse_investigator.config import InvestigatorSettings
from reuse_investigator.report_html import render_html_report
from reuse_investigator.workspace import Workspace


class _ViewerHandler(BaseHTTPRequestHandler):
    workspace: Workspace
    settings: InvestigatorSettings

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        parts = [segment for segment in parsed.path.split("/") if segment]
        query = parse_qs(parsed.query)

        if parsed.path in ("/", "/health"):
            self._send_json(
                200,
                {
                    "service": "reuse-investigator-viewer",
                    "investigations": [
                        report["investigation_id"]
                        for report in self.workspace.list_reports()
                    ],
                },
            )
            return

        if len(parts) == 3 and parts[0:2] == ["v1", "investigations"]:
            investigation_id = parts[2]
            report = self.workspace.read_report(investigation_id)
            if report is None:
                self._send_json(404, {"error": "investigation not found"})
                return
            self._send(
                200,
                render_html_report(report).encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return

        if (
            len(parts) == 4
            and parts[0:2] == ["v1", "investigations"]
            and parts[3] == "artifact"
        ):
            investigation_id = parts[2]
            name = query.get("name", [""])[0]
            if not name or "/" in name or name in (".", ".."):
                self._send_json(400, {"error": "invalid artifact name"})
                return
            try:
                payload = self.workspace.read_frame_evidence(
                    investigation_id,
                    name,
                )
            except FileNotFoundError:
                self._send_json(404, {"error": "artifact not found"})
                return
            self._send(200, payload, "image/x-portable-pixmap")
            return

        if (
            len(parts) == 4
            and parts[0:2] == ["v1", "investigations"]
            and parts[3] == "report"
        ):
            investigation_id = parts[2]
            report = self.workspace.read_report(investigation_id)
            if report is None:
                self._send_json(404, {"error": "investigation not found"})
                return
            self._send_json(200, report)
            return

        self._send_json(404, {"error": "route not found"})


def build_viewer(
    host: str,
    port: int,
    workspace: Workspace,
    settings: InvestigatorSettings,
) -> ThreadingHTTPServer:
    handler = type(
        "BoundViewerHandler",
        (_ViewerHandler,),
        {"workspace": workspace, "settings": settings},
    )
    return ThreadingHTTPServer((host, port), handler)
