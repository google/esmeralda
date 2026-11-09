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

"""Unit tests for Agent Identity Auth Manager (3LO/2LO delegation), MCP claims guard, and --user-token."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from esmeralda import auth, auth_manager, cli, context, mcp, mcp_authz
from esmeralda import query as q
from esmeralda.config import AgentConfig
from esmeralda.report import Report


def _make_jwt(claims: dict) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{header}.{payload}.signature"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    auth.clear_cache()
    for var in (
        "AGENT_AUTH_PROVIDER",
        "AGENT_AUTH_MANAGER_URL",
        "AGENT_SPIFFE_ID",
        "AGENT_WORKLOAD_CERT_PATH",
        "MCP_ENFORCE_USER_AUTH",
        "AUTHZ_MOCK_USER",
        "MCP_ALLOWED_USER_DOMAINS",
        "ESMERALDA_USER_TOKEN",
        "USER_AUTH_ID",
        "SERVICE_ACCOUNT_EMAIL",
        "ESMERALDA_USER_CREDENTIAL_TARGETS",
        "USER_CREDENTIAL_HEADER",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AGENT_NAME", "ai_coe_mortgage_specialist")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    yield
    auth.clear_cache()


# ---------------------------------------------------------------------------
# 1. Agent Identity Auth Manager & Three-Identity Delegation
# ---------------------------------------------------------------------------


def test_delegated_context_and_spiffe_id(monkeypatch, tmp_path):
    assert auth_manager.spiffe_id() is None

    spiffe_uri = (
        "principal://agents.global.org-123456789.system.id.goog/"
        "resources/aiplatform/projects/111222333/locations/us-central1/reasoningEngines/999"
    )
    monkeypatch.setenv("AGENT_SPIFFE_ID", spiffe_uri)
    assert auth_manager.spiffe_id() == spiffe_uri

    ctx = SimpleNamespace(
        session=SimpleNamespace(id="sess-42", user_id="underwriter@example.com", state={}),
    )
    delegated = auth_manager.delegated_context(
        ctx,
        auth_provider="projects/p/locations/global/authProviders/dms-oauth",
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    assert delegated.is_delegated is True
    assert delegated.user_identity == "underwriter@example.com"
    assert delegated.agent_spiffe_id == spiffe_uri
    assert delegated.auth_provider == "projects/p/locations/global/authProviders/dms-oauth"
    assert delegated.scopes == ("https://www.googleapis.com/auth/drive.readonly",)
    assert delegated.to_dict()["user_identity"] == "underwriter@example.com"

    # Workload X.509 certificate fallback when AGENT_SPIFFE_ID is not set
    monkeypatch.delenv("AGENT_SPIFFE_ID")
    cert_file = tmp_path / "certificates.pem"
    cert_file.write_text(f"Subject Alternative Name: URI:{spiffe_uri}\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_WORKLOAD_CERT_PATH", str(cert_file))
    assert auth_manager.spiffe_id() == spiffe_uri


def test_resolve_user_token_prefers_auth_manager_over_invocation_token(monkeypatch):
    monkeypatch.setattr(auth_manager, "acquire_token", lambda *a, **kw: "brokered-3lo-token")
    ctx = SimpleNamespace(state={"temp:user_auth_token": "raw-ge-token"})
    token = auth_manager.resolve_user_token(ctx, auth_provider="projects/p/locations/global/authProviders/dms")
    assert token == "brokered-3lo-token"
    # Without Auth Manager configured, the invocation's own token is used.
    assert auth_manager.resolve_user_token(ctx) == "raw-ge-token"


def test_resolve_user_token_fails_closed_when_auth_manager_fails(monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("Auth Manager down")

    monkeypatch.setattr(auth_manager, "acquire_token", broken)
    ctx = SimpleNamespace(state={"temp:user_auth_token": "raw-ge-token"})
    with pytest.raises(RuntimeError):
        auth_manager.resolve_user_token(ctx, auth_provider="projects/p/locations/global/authProviders/dms")


def test_resolve_user_token_acquires_3lo_token_from_auth_manager(monkeypatch):
    spiffe_uri = (
        "principal://agents.global.org-123456789.system.id.goog/"
        "resources/aiplatform/projects/111222333/locations/us-central1/reasoningEngines/999"
    )
    monkeypatch.setenv("AGENT_SPIFFE_ID", spiffe_uri)
    monkeypatch.setattr(auth_manager, "_runtime_access_token", lambda: "agent-runtime-access-token")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"accessToken": "brokered-3lo-user-token", "expiresIn": "3600s"})

    monkeypatch.setattr(
        auth_manager,
        "http_client",
        lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw),
    )

    ctx = SimpleNamespace(
        session=SimpleNamespace(id="sess-100", user_id="julian.underwriter@example.com", state={}),
    )
    provider = "projects/test-gov/locations/global/authProviders/legacy-dms-3lo"
    token = auth_manager.resolve_user_token(
        ctx,
        auth_provider=provider,
        scopes=["dms.read"],
    )

    assert token == "brokered-3lo-user-token"
    assert captured["url"] == f"https://agentidentitycredentials.googleapis.com/v1alpha/{provider}:acquireToken"
    assert captured["headers"]["authorization"] == "Bearer agent-runtime-access-token"
    assert captured["headers"]["x-agent-spiffe-id"] == spiffe_uri
    assert captured["body"] == {
        "userId": "julian.underwriter@example.com",
        "sessionId": "sess-100",
        "scopes": ["dms.read"],
        "agentSpiffeId": spiffe_uri,
    }


def test_mcp_headers_for_attaches_delegated_user_token_and_spiffe_id(monkeypatch):
    spiffe_uri = (
        "principal://agents.global.org-123456789.system.id.goog/"
        "resources/aiplatform/projects/111222333/locations/us-central1/reasoningEngines/999"
    )
    monkeypatch.setenv("AGENT_SPIFFE_ID", spiffe_uri)
    monkeypatch.setattr(auth, "id_token", lambda audience: f"id-token-for-{audience}")
    monkeypatch.setattr(auth_manager, "_runtime_access_token", lambda: "runtime-tok")
    monkeypatch.setattr(
        auth_manager,
        "http_client",
        lambda **kw: httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"accessToken": "3lo-delegated-tok"})),
            **kw,
        ),
    )

    ctx = SimpleNamespace(user_id="analyst@example.com", state={})
    monkeypatch.setenv("ESMERALDA_USER_CREDENTIAL_TARGETS", '{"legacy-dms.esmeralda.internal": "X-Dms-User"}')
    headers = mcp.headers_for(
        "https://legacy-dms.esmeralda.internal/mcp",
        ctx,
        auth_provider="projects/p/locations/global/authProviders/dms-3lo",
    )

    assert headers["Authorization"] == "Bearer id-token-for-https://legacy-dms.esmeralda.internal"
    assert headers["X-Dms-User"] == "3lo-delegated-tok"
    assert headers["X-Agent-Spiffe-Id"] == spiffe_uri
    assert headers["X-API-Key"] == "ai_coe_mortgage_specialist"

    # A host that isn't listed gets nothing, and Auth Manager isn't even called for it.
    monkeypatch.setattr(auth_manager, "acquire_token", lambda *a, **kw: pytest.fail("Auth Manager called"))
    other = mcp.headers_for(
        "https://corporate-email.esmeralda.internal/mcp",
        ctx,
        auth_provider="projects/p/locations/global/authProviders/dms-3lo",
    )
    assert "3lo-delegated-tok" not in other.values() and "X-Dms-User" not in other


# ---------------------------------------------------------------------------
# 2. Zero-Latency MCP End-User & Agent Delegation Guard (esmeralda.mcp_authz)
# ---------------------------------------------------------------------------


def test_mcp_authz_fails_closed_when_enforced_and_token_missing(monkeypatch):
    monkeypatch.setenv("MCP_ENFORCE_USER_AUTH", "true")
    with pytest.raises(mcp_authz.AuthorizationError) as exc_info:
        mcp_authz.verify_request({"X-API-Key": "ai_coe_mortgage_specialist"})
    assert exc_info.value.status_code == 401
    assert "X-User-Token" in str(exc_info.value)

    # The configured header, the default and the legacy header are all read.
    monkeypatch.setenv("MCP_ENFORCE_USER_AUTH", "false")
    jwt = _make_jwt({"sub": "u-1", "email": "user@example.org", "exp": int(time.time()) + 600})
    for header in ("X-User-Token", "User-Auth-Token"):
        assert mcp_authz.verify_request({header: jwt}).email == "user@example.org"
    monkeypatch.setenv("USER_CREDENTIAL_HEADER", "X-Dms-User")
    assert mcp_authz.verify_request({"X-Dms-User": jwt}).email == "user@example.org"
    assert mcp_authz.user_token_headers() == ["X-Dms-User", "X-User-Token", "User-Auth-Token"]


def test_mcp_authz_rejects_service_account_tokens_masquerading_as_user():
    sa_jwt = _make_jwt(
        {
            "iss": "https://accounts.google.com",
            "sub": "10987654321",
            "email": "sa-esmeralda-mcp-invoker@my-project.iam.gserviceaccount.com",
            "exp": int(time.time()) + 3600,
        }
    )
    with pytest.raises(mcp_authz.AuthorizationError) as exc_info:
        mcp_authz.verify_request({"User-Auth-Token": sa_jwt}, enforce=True)
    assert exc_info.value.status_code == 403
    assert "gserviceaccount.com" in str(exc_info.value)

    # Also rejected when enforce=False (workload SAs must never masquerade as end-user tokens)
    with pytest.raises(mcp_authz.AuthorizationError):
        mcp_authz.verify_request({"User-Auth-Token": sa_jwt}, enforce=False)


def test_mcp_authz_accepts_valid_human_user_jwt_and_spiffe_id():
    spiffe_uri = (
        "principal://agents.global.org-123456789.system.id.goog/"
        "resources/aiplatform/projects/111222333/locations/us-central1/reasoningEngines/999"
    )
    user_jwt = _make_jwt(
        {
            "iss": "https://accounts.google.com",
            "sub": "user-subject-123",
            "email": "developer@example.org",
            "exp": int(time.time()) + 3600,
        }
    )
    verified = mcp_authz.verify_request(
        {
            "User-Auth-Token": f"Bearer {user_jwt}",
            "X-Agent-Spiffe-Id": spiffe_uri,
        },
        enforce=True,
    )
    assert verified.enforced is True
    assert verified.user_identity == "user-subject-123"
    assert verified.email == "developer@example.org"
    assert verified.agent_spiffe_id == spiffe_uri


def test_mcp_authz_rejects_expired_jwt_and_invalid_spiffe_id():
    expired_jwt = _make_jwt(
        {
            "sub": "user-1",
            "email": "developer@example.org",
            "exp": int(time.time()) - 60,
        }
    )
    with pytest.raises(mcp_authz.AuthorizationError) as exc_info:
        mcp_authz.verify_request({"User-Auth-Token": expired_jwt}, enforce=True)
    assert exc_info.value.status_code == 401
    assert "expired" in str(exc_info.value)

    valid_jwt = _make_jwt({"sub": "user-1", "email": "developer@example.org", "exp": int(time.time()) + 600})
    with pytest.raises(mcp_authz.AuthorizationError) as spiffe_exc:
        mcp_authz.verify_request(
            {"User-Auth-Token": valid_jwt, "X-Agent-Spiffe-Id": "https://not-a-spiffe-id"},
            enforce=True,
        )
    assert spiffe_exc.value.status_code == 403


def test_mcp_authz_supports_mock_user_when_not_enforced_and_domain_policy(monkeypatch):
    monkeypatch.setenv("AUTHZ_MOCK_USER", "local.dev@example.org")
    verified = mcp_authz.verify_request({}, enforce=False)
    assert verified.user_identity == "local.dev@example.org"
    assert verified.email == "local.dev@example.org"

    # Domain restriction when explicitly configured via allowed_domains
    user_jwt = _make_jwt({"sub": "u-1", "email": "user@other.org", "exp": int(time.time()) + 600})
    with pytest.raises(mcp_authz.AuthorizationError):
        mcp_authz.verify_request({"User-Auth-Token": user_jwt}, enforce=True, allowed_domains=["example.org"])


# ---------------------------------------------------------------------------
# 3. esmeralda query --user-token / $ESMERALDA_USER_TOKEN propagation
# ---------------------------------------------------------------------------


def test_query_forwards_user_token_to_adk_state_delta(monkeypatch, tmp_path):
    (tmp_path / "agent.yaml").write_text("name: x\nframework: google-adk\n")
    received: dict = {}

    async def server(request: httpx.Request) -> httpx.Response:
        received["body"] = json.loads(request.content)
        line = json.dumps({"author": "root", "content": {"parts": [{"text": "ok"}]}})
        return httpx.Response(200, text=f"{line}\n")

    monkeypatch.setattr(q, "http_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(server), **kw))
    r = Report(stream=io.StringIO(), color=False)
    asyncio.run(
        q.run(
            AgentConfig.load(tmp_path),
            q.Query(
                "verify income",
                caller=context.CallerContext("p", "a"),
                user_token="end-user-oauth-tok",
            ),
            r,
            url="http://localhost:8080",
        )
    )

    assert received["body"]["input"]["state_delta"] == {
        "temp:caller_context": {"project_id": "p", "agent_name": "a"},
        "temp:user_auth_token": "end-user-oauth-tok",
    }


def test_query_forwards_user_token_to_a2a_metadata():
    request = q.Query(
        "verify income",
        caller=context.CallerContext("p", "a"),
        user_token="a2a-end-user-tok",
    )
    assert request.request_metadata() == {
        "caller_context": {"project_id": "p", "agent_name": "a"},
        "user_auth_token": "a2a-end-user-tok",
    }


def test_cli_query_user_token_flag_and_env_var(monkeypatch, tmp_path):
    (tmp_path / "agent.yaml").write_text("name: x\nframework: google-adk\n")
    seen_queries: list[q.Query] = []

    async def fake_run(config, query, report, *, url=None, engine=None):
        seen_queries.append(query)
        report.adk_event({"author": "root", "content": {"parts": [{"text": "done"}]}})

    monkeypatch.setattr(q, "run", fake_run)

    # 1. Explicit --user-token CLI flag
    rc = cli.main(["query", "--agent-dir", str(tmp_path), "--user-token", "cli-flag-token", "hello"])
    assert rc == 0
    assert seen_queries[-1].user_token == "cli-flag-token"

    # 2. Fallback to $ESMERALDA_USER_TOKEN environment variable
    monkeypatch.setenv("ESMERALDA_USER_TOKEN", "env-var-token")
    rc = cli.main(["query", "--agent-dir", str(tmp_path), "hello"])
    assert rc == 0
    assert seen_queries[-1].user_token == "env-var-token"
