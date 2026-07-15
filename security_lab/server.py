from __future__ import annotations

import argparse
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .core import EmailSecurityLab, LabResponse


SAFE_ID = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")


class LabHandler(BaseHTTPRequestHandler):
    server_version = "EmailSecurityLab/1.0"

    @property
    def lab(self) -> EmailSecurityLab:
        return self.server.lab  # type: ignore[attr-defined]

    def _json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 16_384:
            return {}
        try:
            value = json.loads(self.rfile.read(length))
            return value if isinstance(value, dict) else {}
        except (ValueError, UnicodeDecodeError):
            return {}

    def _auth(self) -> str:
        value = self.headers.get("Authorization", "")
        return value[7:] if value.startswith("Bearer ") else ""

    def _ip(self) -> str:
        if os.environ.get("SECURITY_LAB_ALLOW_TEST_IP") == "1":
            test_ip = self.headers.get("X-Lab-IP", "")
            if test_ip:
                return test_ip[:128]
        return self.client_address[0]

    def _identity(self, header: str) -> str:
        value = self.headers.get(header, "")
        return value if SAFE_ID.fullmatch(value) else ""

    def _send(self, response: LabResponse) -> None:
        data = json.dumps(response.body, ensure_ascii=False).encode("utf-8")
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/health":
            self._send(LabResponse(200, {"status": "ok", "scope": "local-security-lab"}))
        else:
            self._send(LabResponse(404, {"error": "not_found"}))

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._json()
        common = {
            "auth_token": self._auth(),
            "ip": self._ip(),
            "device_id": self._identity("X-Device-ID"),
            "session_id": self._identity("X-Session-ID"),
        }
        if path == "/view-email":
            response = self.lab.view_email(
                **common,
                channel_id=str(body.get("channel_id", "")),
                request_id=self._identity("X-Request-ID"),
                captcha_grant=self.headers.get("X-Captcha-Grant", ""),
            )
        elif path == "/captcha/verify":
            response = self.lab.verify_captcha(
                **common,
                challenge_token=str(body.get("challenge_token", "")),
                answer=str(body.get("answer", "")),
            )
        else:
            response = LabResponse(404, {"error": "not_found"})
        self._send(response)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the isolated email rate-limit security lab")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--database", type=Path, default=Path("output/security_lab/lab.sqlite3"))
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    lab = EmailSecurityLab(args.database)
    if args.reset:
        lab.reset()
    server = ThreadingHTTPServer((args.host, args.port), LabHandler)
    server.lab = lab  # type: ignore[attr-defined]
    print(f"Security lab listening on http://{args.host}:{args.port}")
    print("Mock bearer token: lab-token-alice")
    server.serve_forever()


if __name__ == "__main__":
    main()

