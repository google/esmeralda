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

"""Test helpers: throwaway ES256 keys (generated per run, never committed), an IAP-style token minter,
and a fake BigQuery client that records every job."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

API_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[5]
# The suite also runs from the esmeralda package environment (`uv run --package esmeralda`).
if str(API_DIR / "src") not in sys.path:
    sys.path.insert(0, str(API_DIR / "src"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from google.auth import jwt as google_jwt  # noqa: E402
from google.auth.crypt import es256  # noqa: E402

from esmeralda_ui.auth import IAP_ISSUER  # noqa: E402

AUDIENCE = "/projects/123456789/global/backendServices/987654321"
KID = "test-key-1"


class KeyPair:
    def __init__(self, kid: str = KID):
        private = ec.generate_private_key(ec.SECP256R1())
        pem = private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        self.kid = kid
        self.signer = es256.ES256Signer.from_string(pem, key_id=kid)
        self.public_pem = private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()

    def keys(self) -> dict[str, str]:
        return {self.kid: self.public_pem}

    def token(self, **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": IAP_ISSUER,
            "aud": AUDIENCE,
            "sub": "accounts.google.com:1234567890",
            "email": "julian.sterling@example.com",
            "iat": now,
            "exp": now + 600,
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return google_jwt.encode(self.signer, claims).decode()


class FakeRow(dict):
    def items(self):  # google.cloud.bigquery.Row exposes items()
        return super().items()


class FakeJob:
    def __init__(self, rows: list[dict[str, Any]], error: Exception | None = None):
        self._rows = rows
        self._error = error
        self.timeout: float | None = None

    def result(self, timeout: float | None = None):
        self.timeout = timeout
        if self._error:
            raise self._error
        return [FakeRow(r) for r in self._rows]


class FakeBigQueryClient:
    """Records ``(sql, job_config)`` of every query; answers with canned rows per view name."""

    def __init__(self, rows_by_view: dict[str, list[dict[str, Any]]] | None = None, error: Exception | None = None):
        self.rows_by_view = rows_by_view or {}
        self.error = error
        self.jobs: list[tuple[str, Any, FakeJob]] = []

    def query(self, sql: str, job_config: Any = None, **_: Any) -> FakeJob:
        rows = next((r for view, r in self.rows_by_view.items() if f".{view}`" in sql), [])
        job = FakeJob(rows, self.error)
        self.jobs.append((sql, job_config, job))
        return job
