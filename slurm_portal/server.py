from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import socket
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .slurm import PortalError, SlurmClient


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = PROJECT_ROOT / "static"
STATE_DIR = PROJECT_ROOT / ".state"
TOKEN_FILE = STATE_DIR / "access-token"
MAX_BODY = 16 * 1024
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def load_or_create_token() -> str:
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    return token


class PortalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler, client: SlurmClient):
        super().__init__(address, handler)
        self.portal_client = client
        self.access_token = load_or_create_token()
        self.csrf_token = secrets.token_urlsafe(24)
        self.started_at = datetime.now(UTC)


class PortalHandler(BaseHTTPRequestHandler):
    server: PortalHTTPServer
    server_version = "TGMPortal/0.1"

    def log_message(self, fmt: str, *args) -> None:
        logging.info("%s - %s", self.client_address[0], fmt % args)

    def _host_allowed(self) -> bool:
        raw = self.headers.get("Host", "")
        host = raw.rsplit(":", 1)[0].strip("[]") if raw else ""
        return host in ALLOWED_HOSTS

    def _common_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'",
        )

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._common_headers()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int) -> None:
        self._json({"ok": False, "error": message}, status)

    def _authenticated(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        supplied = header[7:].strip()
        return hmac.compare_digest(supplied, self.server.access_token)

    def _csrf_valid(self) -> bool:
        supplied = self.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(supplied, self.server.csrf_token):
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            return urlparse(origin).hostname in ALLOWED_HOSTS
        except ValueError:
            return False

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise PortalError("Invalid request length.") from exc
        if length <= 0 or length > MAX_BODY:
            raise PortalError("Invalid request body.")
        if self.headers.get_content_type() != "application/json":
            raise PortalError("Content-Type must be application/json.", 415)
        try:
            value = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise PortalError("Invalid JSON body.") from exc
        if not isinstance(value, dict):
            raise PortalError("Request body must be an object.")
        return value

    def _serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else unquote(request_path[1:])
        candidate = (STATIC_ROOT / relative).resolve()
        try:
            candidate.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self._error("Not found.", 404)
            return
        if not candidate.is_file():
            self._error("Not found.", 404)
            return
        body = candidate.read_bytes()
        content_type, _ = mimetypes.guess_type(candidate.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self._common_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not self._host_allowed():
            self._error("Invalid host.", 421)
            return
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            self._serve_static(path)
            return
        if not self._authenticated():
            self._error("Access token required.", 401)
            return
        try:
            if path == "/api/health":
                self._json(
                    {
                        "ok": True,
                        "status": "healthy",
                        "host": socket.gethostname(),
                        "user": self.server.portal_client.user,
                        "started_at": self.server.started_at.isoformat(),
                    }
                )
            elif path == "/api/overview":
                payload = self.server.portal_client.overview()
                payload.update(
                    {
                        "ok": True,
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                )
                self._json(payload)
            elif match := re.fullmatch(r"/api/nodes/([A-Za-z0-9._-]+)", path):
                payload = self.server.portal_client.node_detail(match.group(1))
                payload.update(
                    {
                        "ok": True,
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                )
                self._json(payload)
            else:
                self._error("Not found.", 404)
        except PortalError as exc:
            self._error(str(exc), exc.status)
        except Exception:
            logging.exception("Unhandled GET error")
            self._error("Unexpected server error.", 500)

    def do_POST(self) -> None:
        if not self._host_allowed():
            self._error("Invalid host.", 421)
            return
        path = urlparse(self.path).path
        if not self._authenticated():
            self._error("Invalid access token.", 401)
            return
        if path == "/api/auth":
            self._json(
                {
                    "ok": True,
                    "csrf_token": self.server.csrf_token,
                    "user": self.server.portal_client.user,
                    "cluster": "tgmv2",
                }
            )
            return
        if not self._csrf_valid():
            self._error("Invalid CSRF token.", 403)
            return
        try:
            if path == "/api/allocations":
                result = self.server.portal_client.submit_allocation(self._read_json())
                self._json({"ok": True, **result}, 201)
                return
            match = re.fullmatch(r"/api/jobs/(\d+)/cancel", path)
            if match:
                result = self.server.portal_client.cancel_allocation(match.group(1))
                self._json({"ok": True, **result})
                return
            self._error("Not found.", 404)
        except PortalError as exc:
            self._error(str(exc), exc.status)
        except Exception:
            logging.exception("Unhandled POST error")
            self._error("Unexpected server error.", 500)

    def do_OPTIONS(self) -> None:
        self._error("Cross-origin requests are not allowed.", 405)


def main() -> None:
    parser = argparse.ArgumentParser(description="MIL Compute Portal")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORTAL_PORT", "18765")),
    )
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "::1"}:
        raise SystemExit("Refusing to bind outside loopback in the test build.")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    client = SlurmClient(state_dir=STATE_DIR)
    server = PortalHTTPServer((args.host, args.port), PortalHandler, client)
    logging.info(
        "MIL Compute Portal listening on http://%s:%s as %s",
        args.host,
        args.port,
        client.user,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
