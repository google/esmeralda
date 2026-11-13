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

"""esmeralda.auth (cached ID and access tokens), esmeralda.mcp, esmeralda.a2a and the user token."""

import asyncio
from types import MappingProxyType, SimpleNamespace

import httpx
import pytest

from esmeralda import a2a, auth, context, mcp


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    auth.clear_cache()
    for var in ("SERVICE_ACCOUNT_EMAIL", "USER_AUTH_ID", "ACCESS_TOKEN_HOSTS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AGENT_NAME", "test_agent")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    yield
    auth.clear_cache()


class FakeCredentials:
    def __init__(self, name, fail=False):
        self.name, self.fail, self.valid, self.refreshes, self.token = name, fail, False, 0, None

    def refresh(self, request):
        if self.fail:
            raise RuntimeError("impersonation denied")
        self.refreshes += 1
        self.valid, self.token = True, f"{self.name}-token-{self.refreshes}"


@pytest.fixture
def minted(monkeypatch):
    created = []

    def new(audience, service_account):
        creds = FakeCredentials(f"{audience}|{service_account or 'runtime'}", fail=service_account == "denied@sa")
        created.append(creds)
        return creds

    monkeypatch.setattr(auth, "_new_credentials", new)
    return created


# ---- auth -----------------------------------------------------------------------------------


def test_audience_and_local():
    assert auth.audience_for("https://legacy-dms.esmeralda.internal/mcp") == "https://legacy-dms.esmeralda.internal"
    assert auth.audience_for("http://localhost:8003/mcp") == "http://localhost:8003"
    assert auth.is_local("http://localhost:8003/mcp") and auth.is_local("http://127.0.0.1:1")
    assert not auth.is_local("https://x.esmeralda.internal")
    with pytest.raises(ValueError):
        auth.audience_for("/relative")


@pytest.mark.parametrize(
    "url",
    [
        "http://0.0.0.0:8080",  # what `esmeralda serve` prints
        "http://127.0.0.2:8080",
        "http://[::1]:8080",
        "http://LOCALHOST:8080",
        "http://localhost.:8080",
        "http://agent.localhost:8080",
    ],
)
def test_loopback_and_unspecified_addresses_are_local(url):
    assert auth.is_local(url)


@pytest.mark.parametrize(
    "url",
    ["https://x.esmeralda.internal", "http://10.0.0.5:8080", "http://agent:8080", "https://localhost.example.com"],
)
def test_other_hosts_are_remote(url):
    assert not auth.is_local(url)


def test_id_tokens_are_cached_until_expiry(minted):
    assert auth.id_token("https://a") == "https://a|runtime-token-1"
    assert auth.id_token("https://a") == "https://a|runtime-token-1"
    assert len(minted) == 1 and minted[0].refreshes == 1

    minted[0].valid = False  # expired
    assert auth.id_token("https://a") == "https://a|runtime-token-2"
    assert auth.id_token("https://b").startswith("https://b|")
    assert len(minted) == 2


def test_impersonation_and_fallback(minted, monkeypatch):
    monkeypatch.setenv("SERVICE_ACCOUNT_EMAIL", "mcp-invoker@sa")
    assert auth.id_token("https://a") == "https://a|mcp-invoker@sa-token-1"

    monkeypatch.setenv("SERVICE_ACCOUNT_EMAIL", "denied@sa")
    assert auth.id_token("https://a") == "https://a|runtime-token-1"  # falls back to the runtime identity


def test_id_token_async(minted):
    assert asyncio.run(auth.id_token_async("https://a")) == "https://a|runtime-token-1"


# ---- runtime identity ID tokens: never certificate-bound (bind_id_token=False per call) ----


def test_runtime_id_tokens_are_requested_unbound(monkeypatch):
    """Agent Gateway + the load balancer terminate TLS, so a cert-bound ID token fails at Cloud Run."""
    from google.oauth2 import id_token

    calls = []

    def fetch(audience, request=None, bind_id_token=None):
        calls.append((audience, bind_id_token))
        return "unbound-credentials"

    monkeypatch.setattr(id_token, "fetch_id_token_credentials", fetch)
    assert auth._new_credentials("https://legacy-dms.esmeralda.internal", "") == "unbound-credentials"
    assert calls == [("https://legacy-dms.esmeralda.internal", False)]


def test_runtime_id_tokens_with_a_google_auth_without_binding(monkeypatch):
    """google-auth < 2.61 has no bind_id_token (and never binds): called without it."""
    from google.oauth2 import id_token

    calls = []

    def fetch(audience, request=None):
        calls.append(audience)
        return "creds"

    monkeypatch.setattr(id_token, "fetch_id_token_credentials", fetch)
    assert auth._new_credentials("https://a", "") == "creds" and calls == ["https://a"]


def test_runtime_id_tokens_never_touch_the_container_wide_binding_switch(monkeypatch):
    """Access tokens stay bound: the library never sets GOOGLE_API_ENABLE_RUNTIME_BOUND_TOKEN."""
    from google.oauth2 import id_token

    monkeypatch.delenv("GOOGLE_API_ENABLE_RUNTIME_BOUND_TOKEN", raising=False)
    monkeypatch.setattr(id_token, "fetch_id_token_credentials", lambda audience, request=None, bind_id_token=None: "c")
    auth._new_credentials("https://a", "")
    import os

    assert "GOOGLE_API_ENABLE_RUNTIME_BOUND_TOKEN" not in os.environ


def test_impersonation_still_uses_iam_when_opted_in(monkeypatch):
    import google.auth
    from google.auth import impersonated_credentials

    monkeypatch.setattr(google.auth, "default", lambda scopes: (object(), "p"))
    monkeypatch.setattr(impersonated_credentials, "Credentials", lambda **kw: ("target", kw["target_principal"]))
    monkeypatch.setattr(impersonated_credentials, "IDTokenCredentials", lambda **kw: ("id", kw["target_credentials"]))
    assert auth._new_credentials("https://a", "sa@x") == ("id", ("target", "sa@x"))


@pytest.fixture
def minted_access(monkeypatch):
    created = []

    def new(service_account):
        creds = FakeCredentials(f"access|{service_account or 'runtime'}", fail=service_account == "denied@sa")
        created.append(creds)
        return creds

    monkeypatch.setattr(auth, "_new_access_credentials", new)
    return created


def test_access_tokens_are_cached_until_expiry(minted_access):
    assert auth.access_token() == "access|runtime-token-1"
    assert auth.access_token() == "access|runtime-token-1"
    assert len(minted_access) == 1
    minted_access[0].valid = False  # expired
    assert auth.access_token() == "access|runtime-token-2"
    assert len(minted_access) == 1
    auth.clear_cache()
    assert auth.access_token() == "access|runtime-token-1" and len(minted_access) == 2


def test_access_token_impersonation_and_fallback(minted_access, monkeypatch):
    monkeypatch.setenv("SERVICE_ACCOUNT_EMAIL", "mcp-invoker@sa")
    assert auth.access_token() == "access|mcp-invoker@sa-token-1"

    monkeypatch.setenv("SERVICE_ACCOUNT_EMAIL", "denied@sa")
    assert auth.access_token() == "access|runtime-token-1"  # falls back to the runtime identity
    assert asyncio.run(auth.access_token_async()) == "access|runtime-token-1"


def test_access_token_without_impersonation_raises(monkeypatch):
    monkeypatch.setattr(auth, "_new_access_credentials", lambda sa: FakeCredentials("x", fail=True))
    with pytest.raises(RuntimeError):
        auth.access_token()


def test_new_access_credentials_impersonates(monkeypatch):
    import google.auth
    from google.auth import impersonated_credentials

    source = object()
    calls = {}
    monkeypatch.setattr(google.auth, "default", lambda scopes: (source, "p"))

    def fake_impersonated(**kwargs):
        calls.update(kwargs)
        return "impersonated"

    monkeypatch.setattr(impersonated_credentials, "Credentials", fake_impersonated)
    assert auth._new_access_credentials("") is source
    assert auth._new_access_credentials("sa@x") == "impersonated"
    assert calls == {"source_credentials": source, "target_principal": "sa@x", "target_scopes": [auth.CLOUD_SCOPE]}


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://specialist.esmeralda.internal/v1/card", "access"),  # listed
        ("https://SPECIALIST.esmeralda.internal./v1", "access"),  # case and trailing dot
        ("https://other-agent.esmeralda.internal:443", "access"),  # listed with spaces
        ("https://us-central1-aiplatform.googleapis.com/v1beta1/x", "access"),
        ("https://legacy-dms.esmeralda.internal/mcp", "id"),
        ("https://googleapis.com.example.com", "id"),
        ("http://localhost:8080", "id"),
    ],
)
def test_token_kind(monkeypatch, url, kind):
    monkeypatch.setenv("ACCESS_TOKEN_HOSTS", " specialist.esmeralda.internal , Other-Agent.esmeralda.internal.,")
    assert auth.token_kind(url) == kind


def test_token_kind_defaults_to_id_and_reads_env_at_call_time(monkeypatch):
    monkeypatch.delenv("ACCESS_TOKEN_HOSTS", raising=False)
    assert auth.token_kind("https://specialist.esmeralda.internal") == "id"
    monkeypatch.setenv("ACCESS_TOKEN_HOSTS", "specialist.esmeralda.internal")
    assert auth.token_kind("https://specialist.esmeralda.internal") == "access"


def test_bearer_token_dispatch(monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN_HOSTS", "agent.esmeralda.internal")
    monkeypatch.setattr(auth, "id_token", lambda audience: f"id:{audience}")
    monkeypatch.setattr(auth, "access_token", lambda: "access")

    assert auth.bearer_token("https://mcp.esmeralda.internal/mcp") == "id:https://mcp.esmeralda.internal"
    assert auth.bearer_token("https://agent.esmeralda.internal/v1/card") == "access"
    assert auth.bearer_token("https://agent.esmeralda.internal", "id") == "id:https://agent.esmeralda.internal"
    assert auth.bearer_token("https://mcp.esmeralda.internal", "access") == "access"
    assert asyncio.run(auth.bearer_token_async("https://agent.esmeralda.internal")) == "access"
    assert asyncio.run(auth.bearer_token_async("https://mcp.esmeralda.internal/x")).startswith("id:https://mcp.")
    with pytest.raises(ValueError):
        auth.bearer_token("https://mcp.esmeralda.internal", "jwt")
    with pytest.raises(ValueError):
        asyncio.run(auth.bearer_token_async("https://mcp.esmeralda.internal", "jwt"))


# ---- user token -----------------------------------------------------------------------------


def test_user_token_from_gemini_enterprise_state():
    readonly = SimpleNamespace(state=MappingProxyType({"temp:user_auth_token": "ge-token"}))
    invocation = SimpleNamespace(session=SimpleNamespace(state={"temp:user_auth_token": "ge-token"}), run_config=None)
    assert context.user_token(readonly) == context.user_token(invocation) == "ge-token"


def test_user_token_authorization_id_is_configurable(monkeypatch):
    monkeypatch.setenv("USER_AUTH_ID", "corp_sso")
    assert context.user_token(SimpleNamespace(state={"temp:corp_sso": "t"})) == "t"
    assert context.user_token(SimpleNamespace(state={"temp:user_auth_token": "t"})) is None


def test_user_token_from_a2a_metadata_and_absent():
    run_config = SimpleNamespace(custom_metadata={"a2a_metadata": {"user_auth_token": "from-caller"}})
    assert context.user_token(SimpleNamespace(state={}, run_config=run_config)) == "from-caller"
    assert context.user_token(SimpleNamespace(state={"user_auth_token": "persisted"}, run_config=None)) is None


# ---- mcp ------------------------------------------------------------------------------------


def test_mcp_headers(minted, monkeypatch):
    monkeypatch.setenv("ESMERALDA_USER_CREDENTIAL_TARGETS", '{"legacy-dms.esmeralda.internal": null}')
    ctx = SimpleNamespace(state={"temp:user_auth_token": "u-token"})
    remote = mcp.headers_for("https://legacy-dms.esmeralda.internal/mcp", ctx)
    assert remote["Authorization"] == "Bearer https://legacy-dms.esmeralda.internal|runtime-token-1"
    assert remote["X-User-Token"] == "u-token" and remote["X-API-Key"] == "test_agent"

    unlisted = mcp.headers_for("https://corporate-email.esmeralda.internal/mcp", ctx)
    assert "u-token" not in unlisted.values()

    local = mcp.headers_for("http://localhost:8003/mcp", None, api_key="custom")
    assert "Authorization" not in local and "X-User-Token" not in local and local["X-API-Key"] == "custom"


def test_mcp_headers_without_token_still_call(monkeypatch):
    def broken(audience):
        raise RuntimeError("no metadata server")

    monkeypatch.setattr(auth, "id_token", broken)
    assert "Authorization" not in mcp.headers_for("https://x.esmeralda.internal/mcp")


def test_mcp_headers_token_kind(minted, minted_access, monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN_HOSTS", "tools.esmeralda.internal")
    listed = mcp.headers_for("https://tools.esmeralda.internal/mcp")
    assert listed["Authorization"] == "Bearer access|runtime-token-1"
    forced = mcp.headers_for("https://tools.esmeralda.internal/mcp", token="id")
    assert forced["Authorization"] == "Bearer https://tools.esmeralda.internal|runtime-token-1"
    forced = mcp.headers_for("https://legacy-dms.esmeralda.internal/mcp", token="access")
    assert forced["Authorization"] == "Bearer access|runtime-token-1"


def test_mcp_toolset(minted):
    from google.adk.tools.mcp_tool import McpToolset

    toolset = mcp.toolset("http://localhost:8003/mcp", prefix="dms")
    assert isinstance(toolset, McpToolset)
    assert toolset.tool_name_prefix == "dms"


# ---- a2a ------------------------------------------------------------------------------------


def test_a2a_request_metadata(monkeypatch):
    monkeypatch.setenv("ESMERALDA_USER_CREDENTIAL_TARGETS", '{"specialist.esmeralda.internal": null}')
    ic = SimpleNamespace(session=SimpleNamespace(state={"temp:user_auth_token": "u"}), run_config=None)
    assert a2a.request_metadata_for("https://specialist.esmeralda.internal")(ic, None) == {
        "caller_context": {"project_id": "test-project", "agent_name": "test_agent"},
        "user_auth_token": "u",
    }
    # Not listed, or no target at all: the token never goes.
    assert "user_auth_token" not in a2a.request_metadata_for("https://other.esmeralda.internal")(ic, None)
    assert "user_auth_token" not in a2a.request_metadata(ic)
    bare = SimpleNamespace(session=SimpleNamespace(state={}), run_config=None)
    assert "user_auth_token" not in a2a.request_metadata_for("https://specialist.esmeralda.internal")(bare, None)


def test_a2a_auth_hook(minted):
    request = httpx.Request("POST", "https://specialist.esmeralda.internal/v1/message:send")
    asyncio.run(a2a._auth_hook("https://specialist.esmeralda.internal", None)(request))
    assert request.headers["Authorization"] == "Bearer https://specialist.esmeralda.internal|runtime-token-1"
    assert request.headers["X-API-Key"] == "test_agent"

    local = httpx.Request("POST", "http://localhost:8081/v1/message:send")
    asyncio.run(a2a._auth_hook("http://localhost:8081", None)(local))
    assert "Authorization" not in local.headers


def test_a2a_auth_hook_sends_access_token_to_listed_hosts(minted, minted_access, monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN_HOSTS", "specialist.esmeralda.internal")
    request = httpx.Request("POST", "https://specialist.esmeralda.internal/v1/message:send")
    asyncio.run(a2a._auth_hook("https://specialist.esmeralda.internal", None)(request))
    assert request.headers["Authorization"] == "Bearer access|runtime-token-1"

    forced = httpx.Request("POST", "https://specialist.esmeralda.internal/v1/message:send")
    asyncio.run(a2a._auth_hook("https://specialist.esmeralda.internal", None, "id")(forced))
    assert forced.headers["Authorization"] == "Bearer https://specialist.esmeralda.internal|runtime-token-1"

    local = httpx.Request("POST", "http://localhost:8081/v1/message:send")
    asyncio.run(a2a._auth_hook("http://localhost:8081", None, "access")(local))
    assert "Authorization" not in local.headers


def test_a2a_remote_agent_rejects_unknown_token_kind():
    with pytest.raises(ValueError):
        a2a.remote_agent("specialist", url="https://specialist.esmeralda.internal", token="jwt")
    assert a2a.remote_agent("specialist", url="https://specialist.esmeralda.internal", token="access") is not None


@pytest.mark.parametrize("base", ["https://specialist.esmeralda.internal", "http://localhost:8081"])
def test_a2a_card_addresses_are_pinned_to_the_configured_url(base):
    from a2a.types import AgentCapabilities, AgentCard

    agent = a2a.remote_agent("specialist", url=base + "/")
    card = AgentCard(
        name="s",
        description="s",
        version="1",
        url="https://somewhere-else.example.com",
        capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[],
    )
    agent._validate_card_rpc_targets(card)  # ADK's own checks run after pinning
    assert card.url == base
    assert agent._agent_card_source == f"{base}/v1/card"


def test_a2a_card_pinning_keeps_adk_checks():
    from a2a.types import AgentCapabilities, AgentCard
    from google.adk.a2a.agent._remote_a2a_agent import AgentCardResolutionError

    agent = a2a.remote_agent("specialist", url="http://plain-http.example.com")
    card = AgentCard(
        name="s", description="s", version="1", url="https://x", capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"], default_output_modes=["text/plain"], skills=[],
    )  # fmt: skip
    with pytest.raises(AgentCardResolutionError):
        agent._validate_card_rpc_targets(card)  # http to a non-loopback host is still refused
