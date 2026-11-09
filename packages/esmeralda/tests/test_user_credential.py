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

"""esmeralda.user_credential: opt-in targets, header choice, redaction."""

import logging
from types import SimpleNamespace

import pytest

from esmeralda import auth_manager, user_credential
from esmeralda import query as q
from esmeralda.user_credential import REDACTED, Targets

TOKEN = "synthetic-user-cred-7c1e"  # not a real credential


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for var in ("ESMERALDA_USER_CREDENTIAL_TARGETS", "AGENT_AUTH_PROVIDER", "USER_AUTH_ID"):
        monkeypatch.delenv(var, raising=False)


def test_targets_parse_and_match():
    targets = Targets.parse(
        '{"legacy-dms.esmeralda.internal": null, "Income.Esmeralda.Internal": "X-Income-User",'
        ' "localhost:8003": true, "https://email.esmeralda.internal/mcp": "", "off.esmeralda.internal": false}'
    )
    assert targets.header_for("https://legacy-dms.esmeralda.internal/mcp") == "X-User-Token"
    assert targets.header_for("https://income.esmeralda.internal/mcp") == "X-Income-User"
    assert targets.header_for("http://localhost:8003/mcp") == "X-User-Token"
    assert targets.header_for("http://localhost:8004/mcp") is None  # other port
    assert targets.header_for("https://email.esmeralda.internal/anything") == "X-User-Token"
    assert targets.header_for("https://off.esmeralda.internal/mcp") is None
    assert targets.header_for("https://unlisted.esmeralda.internal/mcp") is None
    assert targets.header_for("https://legacy-dms.esmeralda.internal.evil.example/mcp") is None
    assert targets.header_for("not a url") is None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "not json",
        '["legacy-dms.esmeralda.internal"]',
        '{"*": null}',
        '{"*.esmeralda.internal": null}',
        '{"legacy-dms.esmeralda.internal": "Authorization"}',
        '{"legacy-dms.esmeralda.internal": "X-API-Key"}',
        '{"legacy-dms.esmeralda.internal": "baggage"}',
        '{"legacy-dms.esmeralda.internal": "Bad Header"}',
        '{"legacy-dms.esmeralda.internal": 7}',
    ],
)
def test_targets_fail_closed(raw, caplog):
    with caplog.at_level(logging.DEBUG):
        assert Targets.parse(raw).header_for("https://legacy-dms.esmeralda.internal/mcp") is None


def test_targets_env_is_reread(monkeypatch):
    assert not user_credential.accepts("https://legacy-dms.esmeralda.internal/mcp")
    monkeypatch.setenv("ESMERALDA_USER_CREDENTIAL_TARGETS", '{"legacy-dms.esmeralda.internal": null}')
    assert user_credential.accepts("https://legacy-dms.esmeralda.internal/mcp")
    assert user_credential.header_for("https://legacy-dms.esmeralda.internal/mcp") == "X-User-Token"


def test_outgoing_headers_only_for_listed_hosts(monkeypatch):
    monkeypatch.setenv("ESMERALDA_USER_CREDENTIAL_TARGETS", '{"legacy-dms.esmeralda.internal": "X-Dms-User"}')
    ctx = SimpleNamespace(state={"temp:user_auth_token": TOKEN})
    assert user_credential.outgoing_headers("https://legacy-dms.esmeralda.internal/mcp", ctx) == {"X-Dms-User": TOKEN}
    assert user_credential.outgoing_headers("https://email.esmeralda.internal/mcp", ctx) == {}
    no_token = SimpleNamespace(state={})
    assert user_credential.outgoing_headers("https://legacy-dms.esmeralda.internal/mcp", no_token) == {}


def test_outgoing_headers_failure_logs_no_secret(monkeypatch, caplog):
    monkeypatch.setenv("ESMERALDA_USER_CREDENTIAL_TARGETS", '{"legacy-dms.esmeralda.internal": null}')

    def broken(*a, **kw):
        raise RuntimeError(f"upstream echoed {TOKEN}")

    monkeypatch.setattr(auth_manager, "acquire_token", broken)
    ctx = SimpleNamespace(state={"temp:user_auth_token": TOKEN})
    with caplog.at_level(logging.DEBUG):
        headers = user_credential.outgoing_headers("https://legacy-dms.esmeralda.internal/mcp", ctx, auth_provider="p")
    assert headers == {}  # fail closed: no fallback to the raw token
    assert "RuntimeError" in caplog.text and TOKEN not in caplog.text


def test_redact(monkeypatch):
    monkeypatch.setenv("ESMERALDA_USER_CREDENTIAL_TARGETS", '{"legacy-dms.esmeralda.internal": "X-Dms-User"}')
    monkeypatch.setenv("USER_AUTH_ID", "ge_auth")
    value = {
        "temp:ge_auth": "abc",
        "temp:user_auth_token": "abc",
        "X-Dms-User": "abc",
        "Authorization": "Bearer abc",
        "nested": [{"text": f"token is {TOKEN}!"}, (TOKEN,)],
        "count": 3,
    }
    out = user_credential.redact(value, [TOKEN, None, ""])
    assert out == {
        "temp:ge_auth": REDACTED,
        "temp:user_auth_token": REDACTED,
        "X-Dms-User": REDACTED,
        "Authorization": REDACTED,
        "nested": [{"text": f"token is {REDACTED}!"}, (REDACTED,)],
        "count": 3,
    }
    assert value["nested"][0]["text"].endswith(f"{TOKEN}!")  # input untouched
    assert user_credential.describe_error(ValueError(f"bad {TOKEN}"), [TOKEN]) == f"ValueError: bad {REDACTED}"


def test_query_repr_hides_the_token():
    request = q.Query("hi", user_token=TOKEN)
    assert TOKEN not in repr(request)
    assert request.state_delta()["temp:user_auth_token"] == TOKEN  # only temp: state carries it
    assert all(k.startswith("temp:") for k in request.state_delta())
