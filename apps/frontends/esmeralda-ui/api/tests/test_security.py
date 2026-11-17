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

"""HTTP hardening: CSP and security headers on every response, read-only methods, body limits."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from esmeralda_ui.app import create_app
from esmeralda_ui.auth import IAP_HEADER
from esmeralda_ui.security import CONTENT_SECURITY_POLICY
from esmeralda_ui.settings import Settings


@pytest.fixture
def client(cloud_settings, keypair):
    return TestClient(create_app(cloud_settings(demo=True), keys=keypair.keys), raise_server_exceptions=False)


@pytest.fixture
def auth(keypair):
    return {IAP_HEADER: keypair.token()}


def test_csp_is_strict():
    directives = dict(d.split(" ", 1) for d in CONTENT_SECURITY_POLICY.split("; "))
    assert directives["default-src"] == "'self'"
    assert directives["script-src"] == "'self'"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["object-src"] == "'none'"
    assert "unsafe-inline" not in CONTENT_SECURITY_POLICY
    assert "unsafe-eval" not in CONTENT_SECURITY_POLICY
    assert "http" not in CONTENT_SECURITY_POLICY  # no external origins (fonts, CDNs)


@pytest.mark.parametrize("path", ["/healthz", "/api/me", "/api/nope", "/"])
def test_security_headers_on_every_response(client, auth, path):
    response = client.get(path, headers=auth)
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert "strict-transport-security" in response.headers
    assert "server" not in response.headers


def test_security_headers_on_401(client):
    response = client.get("/api/me")
    assert response.status_code == 401
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_only_read_methods(client, auth, method):
    response = client.request(method, "/api/me", headers=auth)
    assert response.status_code == 405
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY


def test_request_body_is_refused(client, auth):
    response = client.request("GET", "/api/me", headers=auth, content=b"x" * 4096)
    assert response.status_code == 413


def test_unknown_api_path_is_generic_404(client, auth):
    response = client.get("/api/does-not-exist", headers=auth)
    assert response.status_code == 404
    assert response.json() == {"error": "not found"}


def test_no_docs_or_openapi_routes(client, auth):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path, headers=auth).status_code == 404


def test_validation_errors_do_not_echo_input(client, auth):
    response = client.get("/api/usage?month=<script>", headers=auth)
    assert response.status_code == 422
    assert response.json() == {"error": "invalid request parameters"}
    assert "script" not in response.text


def test_unexpected_errors_are_generic(cloud_settings, keypair, auth):
    class Exploding:
        def usage(self, month, team):
            raise RuntimeError("secret internal detail: projects/real-project")

    app = create_app(cloud_settings(), keys=keypair.keys, source=Exploding())
    response = TestClient(app, raise_server_exceptions=False).get("/api/usage", headers=auth)
    assert response.status_code == 500
    assert response.json() == {"error": "internal error"}
    assert "secret" not in response.text


def test_local_mode_has_no_hsts():
    client = TestClient(create_app(Settings(mode="local", demo=True)))
    assert "strict-transport-security" not in client.get("/healthz").headers


def test_static_web_is_served_with_csp(tmp_path, cloud_settings, keypair, auth):
    (tmp_path / "index.html").write_text("<!doctype html><title>t</title><div id=root></div>")
    client = TestClient(create_app(cloud_settings(static_dir=tmp_path), keys=keypair.keys))
    response = client.get("/", headers=auth)
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
