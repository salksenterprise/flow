"""Small HTTP adapter for the first ISRP application vertical slice.

The adapter authenticates a development actor from headers, translates JSON to
application use cases, and can serve the compiled React application. Business
and orchestration logic remain in :mod:`isrp.application`.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .application import ISRPApplication
from .orchestration import Actor, ConflictError, NotFoundError, ValidationError, WorkflowError


REQUEST_PATH = re.compile(r"^/api/requests/(\d+)$")
SUBMIT_PATH = re.compile(r"^/api/requests/(\d+)/submit$")
ASSESSMENT_PATH = re.compile(r"^/api/requests/(\d+)/assessments$")
WORK_ACTION_PATH = re.compile(r"^/api/work/(\d+)/actions$")


class ISRPHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], application: ISRPApplication,
                 static_dir: Path | None = None):
        self.application = application
        self.static_dir = static_dir
        super().__init__(address, ISRPHandler)


class ISRPHandler(BaseHTTPRequestHandler):
    server: ISRPHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        if os.getenv("ISRP_HTTP_LOG", "1") != "0":
            super().log_message(format, *args)

    @staticmethod
    def _actor() -> Actor:
        """Development identity supplied by the process, never by the caller."""
        return Actor(
            actor_id=os.getenv("ISRP_DEVELOPMENT_ACTOR", "demo.reviewer"),
            organization_id=os.getenv("ISRP_DEVELOPMENT_ORGANIZATION") or None,
            roles=frozenset(filter(None, (
                item.strip() for item in os.getenv(
                    "ISRP_DEVELOPMENT_ROLES",
                    "REVIEW_COORDINATOR,SECURITY_REVIEWER",
                ).split(",")
            ))),
        )

    def _json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValidationError("Content-Length must be a whole number") from error
        if length <= 0:
            return {}
        if length > 1_000_000:
            raise ValidationError("Request body exceeds 1 MB")
        try:
            value = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValidationError("Request body must be valid JSON") from error
        if not isinstance(value, dict):
            raise ValidationError("Request body must be a JSON object")
        return value

    def _send_json(self, status: int, value: Any) -> None:
        payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _handle_error(self, error: BaseException) -> None:
        if isinstance(error, ValidationError):
            status = HTTPStatus.BAD_REQUEST
        elif isinstance(error, NotFoundError):
            status = HTTPStatus.NOT_FOUND
        elif isinstance(error, ConflictError):
            status = HTTPStatus.CONFLICT
        elif isinstance(error, WorkflowError):
            status = HTTPStatus.UNPROCESSABLE_ENTITY
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        message = str(error) if status != HTTPStatus.INTERNAL_SERVER_ERROR else "Internal server error"
        self._send_json(status, {"error": message, "status": status.value})

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        try:
            if path == "/api/health":
                self._send_json(HTTPStatus.OK, {"status": "ok", "service": "isrp"})
                return
            if path == "/api/dashboard":
                self._send_json(HTTPStatus.OK, self.server.application.dashboard())
                return
            if path == "/api/requests":
                self._send_json(HTTPStatus.OK, self.server.application.list_requests())
                return
            if path == "/api/work":
                self._send_json(HTTPStatus.OK, self.server.application.list_work())
                return
            match = REQUEST_PATH.fullmatch(path)
            if match:
                self._send_json(
                    HTTPStatus.OK, self.server.application.get_request(int(match.group(1))))
                return
            if path.startswith("/api/"):
                raise NotFoundError("API route not found")
            self._serve_static(path)
        except Exception as error:
            self._handle_error(error)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        try:
            body = self._json_body()
            actor = self._actor()
            if path == "/api/requests":
                result = self.server.application.create_request(body, actor)
                self._send_json(HTTPStatus.CREATED, result)
                return
            match = SUBMIT_PATH.fullmatch(path)
            if match:
                result = self.server.application.submit_request(int(match.group(1)), body, actor)
                self._send_json(HTTPStatus.OK, result)
                return
            match = ASSESSMENT_PATH.fullmatch(path)
            if match:
                result = self.server.application.create_assessment(
                    int(match.group(1)), body, actor)
                self._send_json(HTTPStatus.CREATED, result)
                return
            match = WORK_ACTION_PATH.fullmatch(path)
            if match:
                result = self.server.application.apply_work_action(
                    int(match.group(1)), body, actor)
                self._send_json(HTTPStatus.OK, result)
                return
            raise NotFoundError("API route not found")
        except Exception as error:
            self._handle_error(error)

    def _serve_static(self, request_path: str) -> None:
        root = self.server.static_dir
        if root is None or not root.is_dir():
            raise NotFoundError("Frontend build not found; run npm run build in frontend")
        relative = request_path.lstrip("/")
        candidate = (root / relative).resolve() if relative else root / "index.html"
        if root.resolve() not in candidate.parents and candidate != root.resolve():
            raise NotFoundError("Static asset not found")
        if not candidate.is_file():
            candidate = root / "index.html"
        payload = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def make_server(application: ISRPApplication, host: str = "127.0.0.1", port: int = 8000,
                static_dir: str | Path | None = None) -> ISRPHTTPServer:
    root = Path(static_dir).resolve() if static_dir else None
    return ISRPHTTPServer((host, port), application, root)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ISRP application")
    parser.add_argument("--database", default=os.getenv("ISRP_DB_PATH", "isrp.db"))
    parser.add_argument("--host", default=os.getenv("ISRP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ISRP_PORT", "8000")))
    parser.add_argument("--static-dir", default=os.getenv("ISRP_STATIC_DIR", "frontend/dist"))
    args = parser.parse_args()
    application = ISRPApplication(args.database)
    application.initialize()
    server = make_server(application, args.host, args.port, args.static_dir)
    print(f"ISRP listening on http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
