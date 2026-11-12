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

"""Verified caller attribution: the catalog caller guard (offline)."""

from __future__ import annotations

import base64
import json
import logging

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from esmeralda import attribution
from esmeralda.attribution import Allowlist, CallerGuard, decide, verified_principal

VM = "sa-test-vm@example-project.iam.gserviceaccount.com"
SPECIALIST = "sa-specialist@example-project.iam.gserviceaccount.com"
SHARED = "sa-shared-invoker@example-project.iam.gserviceaccount.com"
AGENT = "principal://agents.global.org-0000000000.system.id.goog/resources/aiplatform/projects/1/locations/r/reasoningEngines/2"
STRANGER = "sa-stranger@example-project.iam.gserviceaccount.com"


def _allowlist(unmapped: str = "deny", mismatch: str = "reject") -> Allowlist:
    return Allowlist.from_mapping({
        "policy": {"unmapped_caller": unmapped, "declared_mismatch": mismatch},
        "callers": [
            {"principal": VM, "caller": "test-vm", "team": "platform", "cost_centers": ["cc-platform"]},
            {"principal": SPECIALIST, "caller": "specialist", "team": "ai-coe", "cost_centers": ["cc-ai-coe"]},
            {"principal": AGENT, "caller": "specialist", "team": "ai-coe", "cost_centers": ["cc-ai-coe"]},
            {"principal": SHARED, "caller": "a+b", "team": None, "cost_centers": [], "shared": True},
        ],
    })


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def _stripped(claims: dict) -> str:
    """A Google ID token as Cloud Run hands it to the container (signature removed)."""
    return f"{_b64({'alg': 'RS256'})}.{_b64(claims)}.{attribution.STRIPPED_SIGNATURE}"


def _bearer(claims: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {_stripped(claims)}"}


# ------------------------------------------------------------------------------------------
# reading the verified principal
# ------------------------------------------------------------------------------------------


def test_reads_email_from_the_platform_stripped_token():
    assert verified_principal(_bearer({"email": VM.upper(), "sub": "123"})) == VM


def test_prefers_x_serverless_authorization():
    headers = {**_bearer({"email": STRANGER}), "X-Serverless-Authorization": f"Bearer {_stripped({'email': VM})}"}
    assert verified_principal(headers) == VM


def test_falls_back_to_sub_and_maps_spiffe_to_principal_form():
    token = _stripped({"sub": AGENT.replace("principal://", "spiffe://")})
    assert verified_principal({"authorization": f"Bearer {token}"}) == AGENT
    assert verified_principal(_bearer({"email": VM, "email_verified": False, "sub": "42"})) == "42"


def test_missing_header_is_unverified():
    assert verified_principal({}) is None
    assert verified_principal({"Authorization": ""}) is None


def test_malformed_token_is_unverified():
    assert verified_principal({"Authorization": "Bearer not-a-jwt"}) is None
    assert verified_principal({"Authorization": f"Bearer abc.@@@.{attribution.STRIPPED_SIGNATURE}"}) is None
    assert verified_principal({"Authorization": f"Bearer {_b64({'a': 1})}..{attribution.STRIPPED_SIGNATURE}"}) is None


def test_token_with_a_signature_was_not_validated_by_the_platform():
    token = f"{_b64({'alg': 'RS256'})}.{_b64({'email': VM})}.real-signature"
    assert verified_principal({"Authorization": f"Bearer {token}"}) is None


def test_self_asserted_headers_are_never_used():
    headers = {"X-Agent-Spiffe-Id": AGENT, "X-API-Key": "test-vm", "X-Caller-Principal": VM}
    assert verified_principal(headers) is None
    assert decide(_allowlist(), verified_principal(headers)).reason == "unverified"


# ------------------------------------------------------------------------------------------
# decisions
# ------------------------------------------------------------------------------------------


def test_mapped_principal_is_verified_and_billed_to_its_team():
    d = decide(_allowlist(), VM)
    assert (d.allowed, d.status, d.reason, d.caller, d.team, d.cost_center) == (
        True, 200, "mapped", "test-vm", "platform", "cc-platform")
    assert d.verified
    assert d.caller_record() == {"verified": True, "principal": VM, "team": "platform"}


@pytest.mark.parametrize("mode, allowed, status", [("deny", False, 401), ("flag", True, 200)])
def test_missing_identity(mode, allowed, status):
    d = decide(_allowlist(unmapped=mode), None)
    assert (d.allowed, d.status, d.reason, d.verified) == (allowed, status, "unverified", False)
    assert d.caller_record() == {"verified": False}


@pytest.mark.parametrize("mode, allowed, status", [("deny", False, 403), ("flag", True, 200)])
def test_valid_principal_not_in_the_allowlist(mode, allowed, status):
    d = decide(_allowlist(unmapped=mode), STRANGER)
    assert (d.allowed, d.status, d.reason) == (allowed, status, "unmapped")
    assert not d.verified and d.team is None
    # The principal is authenticated, so it is recorded, but without a team: unattributed.
    assert d.caller_record() == {"verified": False, "principal": STRANGER}


def test_foreign_cost_centre_is_rejected():
    d = decide(_allowlist(mismatch="reject"), VM, "cc-cx-br")
    assert (d.allowed, d.status, d.reason, d.declared_mismatch) == (False, 403, "declared_mismatch", True)


def test_foreign_cost_centre_is_billed_to_the_callers_own_team():
    d = decide(_allowlist(mismatch="bill_own_team"), VM, "cc-cx-br")
    assert (d.allowed, d.status, d.reason, d.declared_mismatch) == (True, 200, "declared_mismatch", True)
    assert (d.team, d.cost_center, d.declared_cost_center) == ("platform", "cc-platform", "cc-cx-br")
    assert d.caller_record() == {"verified": True, "principal": VM, "team": "platform",
                                 "declared": {"cost_center": "cc-cx-br"}}


def test_allowed_cost_centre_is_kept():
    d = decide(_allowlist(), VM, "cc-platform")
    assert (d.reason, d.cost_center, d.declared_mismatch) == ("mapped", "cc-platform", False)


def test_shared_identity_without_a_single_team_is_verified_but_unattributed():
    d = decide(_allowlist(), SHARED)
    assert d.allowed and d.reason == "mapped" and d.team is None
    assert not d.verified


def test_invalid_allowlist_fails_closed_at_startup():
    with pytest.raises(ValueError):
        Allowlist.from_mapping({"policy": {"unmapped_caller": "allow"}, "callers": []})
    with pytest.raises(ValueError):
        Allowlist.from_mapping({"policy": {"declared_mismatch": "ignore"}, "callers": []})
    with pytest.raises(json.JSONDecodeError):
        Allowlist.from_env({attribution.ALLOWLIST_ENV: "{not json"})
    assert Allowlist.from_env({}) is None


# ------------------------------------------------------------------------------------------
# ASGI middleware
# ------------------------------------------------------------------------------------------


def _client(allowlist: Allowlist | None, seen: list) -> TestClient:
    async def probe(request):
        decision = attribution.current()
        seen.append((decision, request.scope.get("state", {}).get("esmeralda.caller")))
        return JSONResponse({"ok": True})

    async def health(request):
        return JSONResponse({"status": "ok"})

    app = Starlette(routes=[Route("/mcp", probe, methods=["POST", "GET"]), Route("/health", health)])
    return TestClient(CallerGuard(app, allowlist))


def test_guard_denies_unknown_and_unverified_callers(caplog):
    seen: list = []
    client = _client(_allowlist(), seen)
    with caplog.at_level(logging.WARNING, logger="esmeralda.attribution"):
        missing = client.post("/mcp")
        stranger = client.post("/mcp", headers=_bearer({"email": STRANGER}))
        malformed = client.post("/mcp", headers={"Authorization": "Bearer abc"})
    assert (missing.status_code, missing.json()["reason"]) == (401, "unverified")
    assert (stranger.status_code, stranger.json()["reason"]) == (403, "unmapped")
    assert malformed.status_code == 401
    assert seen == []  # the app never ran
    assert "abc" not in caplog.text and _stripped({"email": STRANGER}) not in caplog.text
    assert '"reason": "unmapped"' in caplog.text


def test_guard_lets_mapped_callers_through_with_their_team():
    seen: list = []
    response = _client(_allowlist(), seen).post("/mcp", headers=_bearer({"email": VM}))
    assert response.status_code == 200
    assert response.headers[attribution.BILLED_TEAM_HEADER] == "platform"
    (current, in_scope), = seen
    assert current is in_scope and current.team == "platform" and current.verified
    assert attribution.current() is None  # reset after the request


def test_guard_flag_mode_allows_and_marks_unattributed():
    seen: list = []
    client = _client(_allowlist(unmapped="flag"), seen)
    assert client.post("/mcp").status_code == 200
    response = client.post("/mcp", headers=_bearer({"email": STRANGER}))
    assert response.status_code == 200
    assert attribution.BILLED_TEAM_HEADER not in response.headers
    assert [d.reason for d, _ in seen] == ["unverified", "unmapped"]
    assert not any(d.verified for d, _ in seen)


@pytest.mark.parametrize("mode, status, billed", [("reject", 403, None), ("bill_own_team", 200, "platform")])
def test_guard_declared_mismatch_modes(mode, status, billed):
    seen: list = []
    headers = {**_bearer({"email": VM}), attribution.DECLARED_COST_CENTER_HEADER: "cc-cx-br"}
    response = _client(_allowlist(mismatch=mode), seen).post("/mcp", headers=headers)
    assert response.status_code == status
    assert response.headers.get(attribution.BILLED_TEAM_HEADER) == billed


def test_guard_exempts_health_and_is_off_without_an_allowlist(monkeypatch):
    seen: list = []
    assert _client(_allowlist(), seen).get("/health").status_code == 200
    monkeypatch.delenv(attribution.ALLOWLIST_ENV, raising=False)
    client = _client(None, seen)
    assert client.post("/mcp").status_code == 200
    assert seen == [(None, None)]


def test_guard_reads_the_allowlist_from_the_environment(monkeypatch):
    monkeypatch.setenv(attribution.ALLOWLIST_ENV, json.dumps({
        "policy": {"unmapped_caller": "deny", "declared_mismatch": "reject"},
        "callers": [{"principal": VM, "caller": "test-vm", "team": "platform", "cost_centers": ["cc-platform"]}],
    }))
    client = _client(None, [])
    assert client.post("/mcp", headers=_bearer({"email": VM})).status_code == 200
    assert client.post("/mcp", headers=_bearer({"email": STRANGER})).status_code == 403


# ------------------------------------------------------------------------------------------
# usage records: verified fields only from the guard
# ------------------------------------------------------------------------------------------


def test_plugin_caller_uses_the_guard_decision_and_keeps_declared_context():
    from esmeralda import context, plugin

    declared = context.CallerContext(project_id="p", agent_name="cx-agent")
    unguarded = plugin._caller(declared)
    assert unguarded.verified is False and unguarded.principal is None
    assert dict(unguarded.declared) == {"project_id": "p", "agent_name": "cx-agent"}

    token = attribution._current.set(decide(_allowlist(mismatch="bill_own_team"), VM, "cc-cx-br"))
    try:
        caller = plugin._caller(declared)
    finally:
        attribution._current.reset(token)
    assert (caller.verified, caller.principal, caller.team) == (True, VM, "platform")
    assert dict(caller.declared) == {"project_id": "p", "agent_name": "cx-agent", "cost_center": "cc-cx-br"}


def test_identity_evidence_describes_headers_without_values():
    token = _stripped({"email": VM})
    signed = f"{_b64({'alg': 'RS256'})}.{_b64({'email': VM})}.real-signature"
    evidence = attribution.identity_evidence({"Authorization": f"Bearer {signed}", "X-Serverless-Authorization": token})
    assert evidence == {"x-serverless-authorization": "jwt_stripped", "authorization": "jwt_signed"}
    assert attribution.identity_evidence({}) == {"x-serverless-authorization": "absent", "authorization": "absent"}
    assert attribution.identity_evidence({"Authorization": "Bearer opaque-access-token"})["authorization"] == "opaque"
    assert VM not in json.dumps(evidence)


def test_unverified_decisions_log_the_identity_header_shapes(caplog):
    with caplog.at_level(logging.WARNING, logger="esmeralda.attribution"):
        _client(_allowlist(unmapped="flag"), []).post("/mcp", headers={"Authorization": "Bearer opaque-access-token"})
    assert '"identity_headers": {"x-serverless-authorization": "absent", "authorization": "opaque"}' in caplog.text
    assert "opaque-access-token" not in caplog.text


def _signed(claims: dict) -> str:
    return f"{_b64({'alg': 'RS256'})}.{_b64(claims)}.real-signature"


def test_cloud_run_iam_validated_tokens_count_even_when_signed():
    """Behind the internal load balancer Cloud Run forwards the validated token with its signature."""
    headers = {"Authorization": f"Bearer {_signed({'email': VM})}"}
    assert verified_principal(headers) is None  # default: only a platform-stripped token is proof
    assert verified_principal(headers, validated_by=attribution.CLOUD_RUN_IAM) == VM
    # X-Serverless-Authorization is the one Cloud Run validated when both are sent.
    both = {**{"Authorization": f"Bearer {_signed({'email': STRANGER})}"},
            "X-Serverless-Authorization": f"Bearer {_signed({'email': VM})}"}
    assert verified_principal(both, validated_by=attribution.CLOUD_RUN_IAM) == VM
    assert verified_principal({"Authorization": "Bearer opaque-access-token"}, validated_by=attribution.CLOUD_RUN_IAM) is None


def test_guard_uses_the_allowlist_token_validation_setting():
    data = {"policy": {"unmapped_caller": "deny", "declared_mismatch": "reject"},
            "token_validated_by": "cloud_run_iam",
            "callers": [{"principal": VM, "caller": "test-vm", "team": "platform", "cost_centers": ["cc-platform"]}]}
    allowlist = Allowlist.from_mapping(data)
    assert allowlist.token_validated_by == attribution.CLOUD_RUN_IAM
    seen: list = []
    client = _client(allowlist, seen)
    ok = client.post("/mcp", headers={"Authorization": f"Bearer {_signed({'email': VM})}"})
    assert ok.status_code == 200 and ok.headers[attribution.BILLED_TEAM_HEADER] == "platform"
    mismatch = client.post("/mcp", headers={"Authorization": f"Bearer {_signed({'email': VM})}",
                                            attribution.DECLARED_COST_CENTER_HEADER: "cc-cx-br"})
    assert mismatch.status_code == 403
    # Without the setting the same signed token is unverified.
    strict = _client(Allowlist.from_mapping({**data, "token_validated_by": None}), [])
    assert strict.post("/mcp", headers={"Authorization": f"Bearer {_signed({'email': VM})}"}).status_code == 401
    with pytest.raises(ValueError):
        Allowlist.from_mapping({**data, "token_validated_by": "anything"})


def test_x_serverless_authorization_wins_over_a_forged_authorization():
    """(a) Cloud Run validated X-Serverless-Authorization; Authorization passed through untouched."""
    forged = _signed({"email": SPECIALIST})
    headers = {"X-Serverless-Authorization": f"Bearer {_signed({'email': VM})}", "Authorization": f"Bearer {forged}"}
    assert verified_principal(headers, validated_by=attribution.CLOUD_RUN_IAM) == VM
    d = decide(_allowlist(), verified_principal(headers, validated_by=attribution.CLOUD_RUN_IAM))
    assert (d.caller, d.team) == ("test-vm", "platform")


def test_unparseable_x_serverless_authorization_never_falls_back_to_authorization():
    """(b) X-Serverless-Authorization present but unusable: unverified, Authorization is not read."""
    valid_looking = f"Bearer {_signed({'email': VM})}"
    for bad in ("Bearer not-a-jwt", "Bearer abc.@@@.sig", "", "   "):
        headers = {"X-Serverless-Authorization": bad, "Authorization": valid_looking}
        assert verified_principal(headers, validated_by=attribution.CLOUD_RUN_IAM) is None, bad
        assert verified_principal(headers) is None, bad
    allowlist = Allowlist.from_mapping({"policy": {"unmapped_caller": "deny", "declared_mismatch": "reject"},
                                        "token_validated_by": "cloud_run_iam",
                                        "callers": [{"principal": VM, "team": "platform"}]})
    client = _client(allowlist, [])
    response = client.post("/mcp", headers={"X-Serverless-Authorization": "Bearer not-a-jwt",
                                            "Authorization": valid_looking})
    assert (response.status_code, response.json()["reason"]) == (401, "unverified")
