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

"""Identity: IAP JWT verification (signature, issuer, audience, expiry) and the 401 paths."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from ui_testkit import AUDIENCE, KeyPair

from esmeralda_ui.app import create_app
from esmeralda_ui.auth import IAP_HEADER, AuthError, IapPublicKeys, verify_iap_jwt
from esmeralda_ui.settings import Settings, SettingsError


def test_valid_token_yields_identity(keypair):
    identity = verify_iap_jwt(keypair.token(), audience=AUDIENCE, keys=keypair.keys)
    assert identity.email == "julian.sterling@example.com"
    assert identity.auth == "iap"


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "/projects/1/global/backendServices/other"},
        {"iss": "https://accounts.google.com"},
        {"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200},
        {"email": None},
        {"sub": None},
    ],
    ids=["wrong-audience", "wrong-issuer", "expired", "no-email", "no-subject"],
)
def test_invalid_claims_are_rejected(keypair, claims):
    with pytest.raises(AuthError):
        verify_iap_jwt(keypair.token(**claims), audience=AUDIENCE, keys=keypair.keys)


def test_signature_from_another_key_is_rejected(keypair):
    forged = KeyPair(kid=keypair.kid).token()  # same kid, different private key
    with pytest.raises(AuthError):
        verify_iap_jwt(forged, audience=AUDIENCE, keys=keypair.keys)


def test_unknown_key_id_is_rejected(keypair):
    other = KeyPair(kid="unknown-kid").token()
    with pytest.raises(AuthError):
        verify_iap_jwt(other, audience=AUDIENCE, keys=keypair.keys)


@pytest.mark.parametrize("token", ["", "abc", "a.b", "a.b.c.d", "x" * 9000])
def test_malformed_tokens_are_rejected(keypair, token):
    with pytest.raises(AuthError):
        verify_iap_jwt(token, audience=AUDIENCE, keys=keypair.keys)


def test_unsigned_alg_none_token_is_rejected(keypair):
    import base64
    import json

    def b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    payload = {"iss": "https://cloud.google.com/iap", "aud": AUDIENCE, "email": "x@example.com", "sub": "x"}
    token = f"{b64({'alg': 'none', 'kid': keypair.kid})}.{b64(payload)}."
    with pytest.raises(AuthError):
        verify_iap_jwt(token, audience=AUDIENCE, keys=keypair.keys)


def test_public_keys_are_cached():
    calls = []

    def fetch():
        calls.append(1)
        return {"k": "pem"}

    keys = IapPublicKeys(fetch=fetch, ttl=3600)
    assert keys() == {"k": "pem"}
    assert keys() == {"k": "pem"}
    assert len(calls) == 1


def _client(settings: Settings, keypair: KeyPair) -> TestClient:
    return TestClient(create_app(settings, keys=keypair.keys))


@pytest.mark.parametrize("path", ["/api/me", "/api/catalog", "/api/usage", "/api/audit", "/api/traces", "/"])
def test_missing_assertion_is_401(cloud_settings, keypair, path):
    response = _client(cloud_settings(demo=True), keypair).get(path)
    assert response.status_code == 401
    assert response.json() == {"error": "unauthenticated"}


def test_invalid_assertion_is_401_with_generic_body(cloud_settings, keypair):
    client = _client(cloud_settings(), keypair)
    response = client.get("/api/me", headers={IAP_HEADER: keypair.token(aud="wrong")})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthenticated"}


def test_email_header_alone_does_not_authenticate(cloud_settings, keypair):
    client = _client(cloud_settings(), keypair)
    response = client.get("/api/me", headers={"x-goog-authenticated-user-email": "accounts.google.com:a@example.com"})
    assert response.status_code == 401


def test_key_fetch_failure_is_401(cloud_settings):
    def broken():
        raise RuntimeError("network down")

    client = TestClient(create_app(cloud_settings(), keys=broken))
    response = client.get("/api/me", headers={IAP_HEADER: KeyPair().token()})
    assert response.status_code == 401


def test_valid_assertion_reaches_me(cloud_settings, keypair):
    client = _client(cloud_settings(), keypair)
    response = client.get("/api/me", headers={IAP_HEADER: keypair.token()})
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "julian.sterling@example.com"
    assert body["auth"] == "iap"
    assert body["demo"] is False
    assert body["data_source"] == "unconfigured"


def test_healthz_is_public(cloud_settings, keypair):
    response = _client(cloud_settings(), keypair).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cloud_mode_requires_audience():
    with pytest.raises(SettingsError):
        Settings(mode="cloud", iap_audience=None)


def test_local_mode_identity_is_labelled(keypair):
    client = _client(Settings(mode="local", demo=True), keypair)
    body = client.get("/api/me").json()
    assert body["auth"] == "local-dev"
    assert body["demo"] is True


def test_local_mode_refuses_cloud_run():
    with pytest.raises(SettingsError):
        Settings.from_env({"ESMERALDA_UI_MODE": "local", "K_SERVICE": "esmeralda-ui"})


def test_default_mode_is_cloud_and_needs_audience():
    with pytest.raises(SettingsError):
        Settings.from_env({})
    settings = Settings.from_env({"ESMERALDA_UI_IAP_AUDIENCE": AUDIENCE})
    assert settings.mode == "cloud" and not settings.demo
