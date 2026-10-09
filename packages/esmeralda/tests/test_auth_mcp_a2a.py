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

"""esmeralda.auth (cached ID tokens), esmeralda.mcp, esmeralda.a2a and the user token."""

import asyncio
from types import MappingProxyType, SimpleNamespace

import httpx
import pytest

from esmeralda import a2a, auth, context, mcp


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    auth.clear_cache()
    for var in ("SERVICE_ACCOUNT_EMAIL", "USER_AUTH_ID"):
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


def test_mcp_headers(minted):
    ctx = SimpleNamespace(state={"temp:user_auth_token": "u-token"})
    remote = mcp.headers_for("https://legacy-dms.esmeralda.internal/mcp", ctx)
    assert remote["Authorization"] == "Bearer https://legacy-dms.esmeralda.internal|runtime-token-1"
    assert remote["User-Auth-Token"] == "u-token" and remote["X-API-Key"] == "test_agent"

    local = mcp.headers_for("http://localhost:8003/mcp", None, api_key="custom")
    assert "Authorization" not in local and "User-Auth-Token" not in local and local["X-API-Key"] == "custom"


def test_mcp_headers_without_token_still_call(monkeypatch):
    def broken(audience):
        raise RuntimeError("no metadata server")

    monkeypatch.setattr(auth, "id_token", broken)
    assert "Authorization" not in mcp.headers_for("https://x.esmeralda.internal/mcp")


def test_mcp_toolset(minted):
    from google.adk.tools.mcp_tool import McpToolset

    toolset = mcp.toolset("http://localhost:8003/mcp", prefix="dms")
    assert isinstance(toolset, McpToolset)
    assert toolset.tool_name_prefix == "dms"


# ---- a2a ------------------------------------------------------------------------------------


def test_a2a_request_metadata():
    ic = SimpleNamespace(session=SimpleNamespace(state={"temp:user_auth_token": "u"}), run_config=None)
    assert a2a.request_metadata(ic) == {
        "caller_context": {"project_id": "test-project", "agent_name": "test_agent"},
        "user_auth_token": "u",
    }
    bare = SimpleNamespace(session=SimpleNamespace(state={}), run_config=None)
    assert "user_auth_token" not in a2a.request_metadata(bare)


def test_a2a_auth_hook(minted):
    request = httpx.Request("POST", "https://specialist.esmeralda.internal/v1/message:send")
    asyncio.run(a2a._auth_hook("https://specialist.esmeralda.internal", None)(request))
    assert request.headers["Authorization"] == "Bearer https://specialist.esmeralda.internal|runtime-token-1"
    assert request.headers["X-API-Key"] == "test_agent"

    local = httpx.Request("POST", "http://localhost:8081/v1/message:send")
    asyncio.run(a2a._auth_hook("http://localhost:8081", None)(local))
    assert "Authorization" not in local.headers


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
