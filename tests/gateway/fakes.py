# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""In-process fakes for the gateway harness: a test PKI, the GCE metadata server and TLS upstreams.

The metadata contract follows Envoy's gcp_authn client (v1.40): ``GET .../default/identity?audience=``
returns a JWT whose ``exp`` drives the token cache; ``GET .../default/token`` returns JSON with
``access_token`` and ``expires_in``; both require ``Metadata-Flavor: Google``.
"""

from __future__ import annotations

import base64
import collections
import dataclasses
import datetime
import http.server
import json
import pathlib
import re
import ssl
import threading
import time
import urllib.parse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

# --------------------------------------------------------------------------------------------------
# Test PKI
# --------------------------------------------------------------------------------------------------


@dataclasses.dataclass
class CertificateAuthority:
    cert: x509.Certificate
    key: ec.EllipticCurvePrivateKey

    @property
    def pem(self) -> bytes:
        return self.cert.public_bytes(serialization.Encoding.PEM)


def _validity() -> tuple[datetime.datetime, datetime.datetime]:
    now = datetime.datetime.now(datetime.timezone.utc)
    return now - datetime.timedelta(minutes=5), now + datetime.timedelta(days=1)


def make_ca(name: str) -> CertificateAuthority:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    not_before, not_after = _validity()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return CertificateAuthority(cert, key)


def make_leaf(ca: CertificateAuthority, san: str, directory: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A server certificate for DNS name ``san`` signed by ``ca``; returns (cert file, key file)."""
    key = ec.generate_private_key(ec.SECP256R1())
    not_before, not_after = _validity()
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, san)]))
        .issuer_name(ca.cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.key.public_key()), critical=False)
        .sign(ca.key, hashes.SHA256())
    )
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{san}-{cert.serial_number:x}"
    cert_file, key_file = directory / f"{stem}.crt", directory / f"{stem}.key"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return cert_file, key_file


# --------------------------------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def fake_id_token(audience: str, lifetime_s: int = 3600) -> str:
    """A JWT-shaped ID token (unsigned) that Envoy's JWT parser accepts; ``exp`` drives its cache."""
    now = int(time.time())
    header = {"alg": "RS256", "kid": "gateway-harness", "typ": "JWT"}
    claims = {
        "iss": "https://accounts.google.com",
        "aud": audience,
        "sub": "harness",
        "iat": now,
        "exp": now + lifetime_s,
    }
    return ".".join(
        [_b64url(json.dumps(header).encode()), _b64url(json.dumps(claims).encode()), _b64url(b"harness-signature")]
    )


def jwt_claims(token: str) -> dict:
    """Decoded JWT payload (signature not checked)."""
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


# --------------------------------------------------------------------------------------------------
# Servers
# --------------------------------------------------------------------------------------------------


class _QuietHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # keep pytest output clean
        pass

    def reply(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _BackgroundServer:
    def __init__(self, server: http.server.ThreadingHTTPServer):
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()


class FakeMetadataServer(_BackgroundServer):
    """The two GCE metadata endpoints gcp_authn calls, counting calls per token kind."""

    ACCESS_TOKEN = "ya29.gateway-harness-access-token"
    IDENTITY_PATH = "/computeMetadata/v1/instance/service-accounts/default/identity"
    TOKEN_PATH = "/computeMetadata/v1/instance/service-accounts/default/token"

    def __init__(self, port: int):
        self.port = port
        self.id_calls: collections.Counter[str] = collections.Counter()
        self.access_calls = 0
        self.unexpected: list[str] = []
        self._lock = threading.Lock()
        fake = self

        class Handler(_QuietHandler):
            def do_GET(self) -> None:
                parsed = urllib.parse.urlsplit(self.path)
                if self.headers.get("Metadata-Flavor") != "Google":
                    return self.reply(403, b"Missing Metadata-Flavor:Google header.", "text/plain")
                if parsed.path == fake.IDENTITY_PATH:
                    audience = urllib.parse.parse_qs(parsed.query).get("audience", [""])[0]
                    with fake._lock:
                        fake.id_calls[audience] += 1
                    return self.reply(200, fake_id_token(audience).encode(), "text/plain")
                if parsed.path == fake.TOKEN_PATH:
                    with fake._lock:
                        fake.access_calls += 1
                    token = {"access_token": fake.ACCESS_TOKEN, "expires_in": 3599, "token_type": "Bearer"}
                    return self.reply(200, json.dumps(token).encode(), "application/json")
                with fake._lock:
                    fake.unexpected.append(self.path)
                return self.reply(404, b"not found", "text/plain")

        super().__init__(http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler))


@dataclasses.dataclass
class Recorded:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]  # lower-case names; the last value if a header was repeated
    body: bytes
    header_lines: list[tuple[str, str]] = dataclasses.field(default_factory=list)  # every line, in order

    def header_values(self, name: str) -> list[str]:
        return [value for line_name, value in self.header_lines if line_name == name.lower()]


_BEARER = re.compile(r"Bearer ([A-Za-z0-9._~+/=-]+)")


def bearer(recorded: Recorded) -> str:
    """The token of the request's only ``Authorization`` header, which must hold exactly one bearer token.

    Strict on purpose: a caller token sent next to the gateway's, as a second header or comma-joined into
    the same one, fails the check.
    """
    values = recorded.header_values("authorization")
    assert len(values) == 1, f"expected exactly one Authorization header, got {values!r}"
    match = _BEARER.fullmatch(values[0])
    assert match, f"not a single bearer token: {values[0]!r}"
    return match.group(1)


class MockUpstream(_BackgroundServer):
    """HTTPS upstream on 127.0.0.1 that records every request; answers JSON, optionally slowly or as SSE."""

    def __init__(self, port: int, cert_file: pathlib.Path, key_file: pathlib.Path):
        self.port = port
        self.url = f"https://localhost:{port}"
        self.requests: list[Recorded] = []
        self.delay_s = 0.0
        self.sse_events = 0
        self.sse_gap_s = 0.0
        self._lock = threading.Lock()
        upstream = self

        class Handler(_QuietHandler):
            def handle_any(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                parsed = urllib.parse.urlsplit(self.path)
                recorded = Recorded(
                    method=self.command,
                    path=parsed.path,
                    query=urllib.parse.parse_qs(parsed.query),
                    headers={name.lower(): value for name, value in self.headers.items()},
                    body=body,
                    header_lines=[(name.lower(), value) for name, value in self.headers.items()],
                )
                with upstream._lock:
                    upstream.requests.append(recorded)
                if upstream.sse_events:
                    return self.stream_sse(upstream.sse_events, upstream.sse_gap_s)
                if upstream.delay_s:
                    time.sleep(upstream.delay_s)
                self.reply(200, json.dumps({"method": self.command, "path": parsed.path}).encode(), "application/json")

            def stream_sse(self, events: int, gap_s: float) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for index in range(events):
                    if index:
                        time.sleep(gap_s)
                    chunk = f"data: {json.dumps({'event': index})}\n\n".encode()
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_any

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_file, key_file)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        super().__init__(server)

    @property
    def last(self) -> Recorded:
        with self._lock:
            assert self.requests, f"upstream on port {self.port} received no request"
            return self.requests[-1]

    def reset(self) -> None:
        with self._lock:
            self.requests.clear()
        self.delay_s, self.sse_events, self.sse_gap_s = 0.0, 0, 0.0
