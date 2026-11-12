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

"""Offline conformance tests for the llm-gateway proxy (agent -> model hop through Apigee)."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from apigee_harness import run_apigee_js

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE = REPO_ROOT / "apps" / "apigee" / "proxies" / "llm-gateway" / "apiproxy"
AUTHORIZE_JS = BUNDLE / "resources" / "jsc" / "authorize-llm-call.js"
EXTRACT_TOKENS_JS = BUNDLE / "resources" / "jsc" / "extract-tokens.js"

CALLER = "sa-cx-orchestrator@example-project.iam.gserviceaccount.com"
PROJECT = "esm-dev-cx-agents-mock"
MODEL_PATH = f"/v1beta1/projects/{PROJECT}/locations/us-central1/publishers/google/models/gemini-2.5-flash"


def _xml(relative: str) -> ET.Element:
    return ET.parse(BUNDLE / relative).getroot()


def _steps(root: ET.Element, xpath: str) -> list[tuple[str, str]]:
    return [
        ((step.findtext("Name") or "").strip(), (step.findtext("Condition") or "").strip())
        for step in root.findall(xpath)
    ]


def test_bundle_manifest_matches_files() -> None:
    """Every policy, resource and endpoint listed in llm-gateway.xml exists, and nothing is unlisted."""
    manifest = _xml("llm-gateway.xml")
    assert manifest.attrib["name"] == "llm-gateway"
    assert (manifest.findtext("BasePaths") or "").strip() == "/llm"

    listed_policies = {(p.text or "").strip() for p in manifest.findall("./Policies/Policy")}
    policy_files = {path.stem for path in (BUNDLE / "policies").glob("*.xml")}
    assert listed_policies == policy_files
    for path in (BUNDLE / "policies").glob("*.xml"):
        assert ET.parse(path).getroot().attrib.get("name") == path.stem

    listed_resources = {(r.text or "").strip() for r in manifest.findall("./Resources/Resource")}
    assert listed_resources == {f"jsc://{p.name}" for p in (BUNDLE / "resources" / "jsc").glob("*.js")}

    assert {(t.text or "").strip() for t in manifest.findall("./TargetEndpoints/TargetEndpoint")} == {"default", "sse"}
    assert {p.stem for p in (BUNDLE / "targets").glob("*.xml")} == {"default", "sse"}


def test_required_policies_present() -> None:
    """Caller verification, per-caller token quota, analytics capture and usage logging are all in the bundle."""
    kinds = {path.stem: ET.parse(path).getroot().tag for path in (BUNDLE / "policies").glob("*.xml")}
    assert kinds["VJ-Verify-Caller-Token"] == "VerifyJWT"
    assert kinds["LTQ-enforce"] == "LLMTokenQuota"
    assert kinds["LTQ-count"] == "LLMTokenQuota"
    assert kinds["DC-capture-llm-metrics"] == "DataCapture"
    assert kinds["ML-interaction"] == "MessageLogging"
    assert kinds["KVM-Get-LLM-Gateway-Config"] == "KeyValueMapOperations"
    assert not any(kind == "VerifyAPIKey" for kind in kinds.values()), "agents authenticate with ID tokens, not keys"


def test_preflow_verifies_and_authorizes_before_anything_else() -> None:
    proxy = _xml("proxies/default.xml")
    names = [name for name, _ in _steps(proxy, "./PreFlow/Request/Step")]
    assert names[:3] == ["KVM-Get-LLM-Gateway-Config", "VJ-Verify-Caller-Token", "JS-Authorize-LLM-Call"]
    # Rejections run before the quota and before credentials are swapped.
    for rejection in ("RF-Caller-Not-Allowed", "RF-Not-Found", "RF-Target-Not-Allowed"):
        assert names.index(rejection) < names.index("LTQ-enforce") < names.index("AM-Strip-Caller-Credentials")
    conditions = dict(_steps(proxy, "./PreFlow/Request/Step"))
    assert conditions["RF-Caller-Not-Allowed"] == 'llmgw.denied.reason = "caller_not_allowed"'
    assert conditions["RF-Target-Not-Allowed"] == 'llmgw.denied.reason = "target_not_allowed"'
    assert conditions["VJ-Verify-Caller-Token"] == "" and conditions["LTQ-enforce"] == ""

    fault_rule = proxy.find("./DefaultFaultRule")
    assert fault_rule is not None and (fault_rule.findtext("AlwaysEnforce") or "").strip() == "true"
    assert [name for name, _ in _steps(fault_rule, "./Step")] == ["JS-Usage-Context-Fault"]
    assert [name for name, _ in _steps(proxy, "./PostClientFlow/Response/Step")] == ["ML-interaction"]

    routes = {r.attrib["name"]: (r.findtext("Condition") or "").strip() for r in proxy.findall("./RouteRule")}
    assert routes == {"sse": "llm.stream = true", "default": ""}


def test_caller_token_is_a_google_id_token_bound_to_the_gateway() -> None:
    vj = _xml("policies/VJ-Verify-Caller-Token.xml")
    assert vj.attrib.get("continueOnError") == "false"
    assert (vj.findtext("Algorithm") or "").strip() == "RS256"
    assert vj.find("Source") is None, "the token is read from Authorization: Bearer"
    jwks = vj.find("./PublicKey/JWKS")
    assert jwks is not None and jwks.attrib.get("uri") == "https://www.googleapis.com/oauth2/v3/certs"
    assert (vj.findtext("Issuer") or "").strip() == "https://accounts.google.com"
    audience = vj.find("Audience")
    assert audience is not None and audience.attrib.get("ref") == "llmgw.audience"


def test_quota_is_per_verified_caller() -> None:
    for name in ("LTQ-enforce", "LTQ-count"):
        ltq = _xml(f"policies/{name}.xml")
        identifier = ltq.find("Identifier")
        assert identifier is not None and identifier.attrib.get("ref") == "llmgw.caller.principal"
        assert (ltq.findtext("SharedName") or "").strip() == "esmeralda-llm-gateway-quota"
        assert (ltq.findtext("LLMModelSource") or "").strip() == "{llm.request.model}"
    assert (_xml("policies/LTQ-count.xml").findtext("LLMTokenUsageSource") or "").strip() == "{llm.totalTokenCount}"


@pytest.mark.parametrize("target", ["default", "sse"])
def test_targets_use_google_access_token_and_strict_tls(target: str) -> None:
    root = _xml(f"targets/{target}.xml")
    conn = root.find("./HTTPTargetConnection")
    assert conn is not None
    assert conn.find("./Authentication/GoogleAccessToken") is not None
    assert conn.find("./Authentication/GoogleIDToken") is None
    assert (conn.findtext("./SSLInfo/Enabled") or "").strip() == "true"
    assert (conn.findtext("./SSLInfo/IgnoreValidationErrors") or "").strip() == "false"
    assert [name for name, _ in _steps(root, "./PreFlow/Request/Step")] == ["AM-Set-Target-Url"]


def test_sse_target_meters_on_the_final_event_without_buffering() -> None:
    sse = _xml("targets/sse.xml")
    steps = _steps(sse, "./EventFlow/Response/Step")
    assert sse.find("./EventFlow").attrib.get("content-type") == "text/event-stream"
    assert steps[0] == ("JS-extract-tokens", "")
    assert dict(steps)["LTQ-count"] == "isFinal = true"
    assert dict(steps)["DC-capture-llm-metrics"] == "isFinal = true"
    assert dict(steps)["JS-Usage-Context"] == "isFinal = true"
    assert not _steps(sse, "./PostFlow/Response/Step"), "no response PostFlow policy may touch the stream"


def test_caller_credentials_never_reach_vertex() -> None:
    strip = _xml("policies/AM-Strip-Caller-Credentials.xml")
    removed = {h.attrib["name"].lower() for h in strip.findall("./Remove/Headers/Header")}
    assert {"authorization", "x-forwarded-authorization", "x-api-key"} <= removed
    set_url = _xml("policies/AM-Set-Target-Url.xml")
    assigned = {(a.findtext("Name") or "").strip(): a for a in set_url.findall("./AssignVariable")}
    assert (assigned["target.url"].findtext("Ref") or "").strip() == "llm.target.url"
    assert (assigned["target.copy.pathsuffix"].findtext("Value") or "").strip() == "false"


def _authorize(**overrides: Any) -> dict[str, Any]:
    flow: dict[str, Any] = {
        "jwt.VJ-Verify-Caller-Token.claim.subject": "112233445566778899",
        "jwt.VJ-Verify-Caller-Token.claim.email": CALLER,
        "jwt.VJ-Verify-Caller-Token.claim.email_verified": "true",
        "llmgw.allowed_callers": f"{CALLER}, sa-other@example-project.iam.gserviceaccount.com",
        "llmgw.allowed_projects": PROJECT,
        "llmgw.allowed_locations": "us-central1,global",
        "llmgw.allowed_models": "gemini-*",
        "proxy.pathsuffix": f"{MODEL_PATH}:generateContent",
        "request.verb": "POST",
    }
    flow.update(overrides)
    return run_apigee_js(AUTHORIZE_JS, {k: v for k, v in flow.items() if v is not None})


def test_authorize_allows_registered_caller() -> None:
    state = _authorize()
    assert state["llmgw.denied.reason"] == ""
    assert state["llmgw.caller.principal"] == CALLER
    assert state["consumer.app.name"] == CALLER
    assert state["llm.request.model"] == "gemini-2.5-flash"
    assert state["llm.method"] == "generateContent"
    assert state["llm.stream"] is False
    assert state["llm.target.url"] == f"https://us-central1-aiplatform.googleapis.com{MODEL_PATH}:generateContent"


def test_authorize_streaming_and_global_location() -> None:
    path = MODEL_PATH.replace("us-central1", "global") + ":streamGenerateContent"
    state = _authorize(**{"proxy.pathsuffix": path})
    assert state["llmgw.denied.reason"] == ""
    assert state["llm.stream"] is True
    assert state["llm.target.url"] == f"https://aiplatform.googleapis.com{path}"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"jwt.VJ-Verify-Caller-Token.claim.email": "intruder@example.com"}, "caller_not_allowed"),
        ({"llmgw.allowed_callers": "none"}, "caller_not_allowed"),
        ({"llmgw.allowed_callers": "*"}, "caller_not_allowed"),  # no wildcard callers: fail closed
        (
            # an unverified email is ignored; the subject is not registered
            {"jwt.VJ-Verify-Caller-Token.claim.email_verified": "false"},
            "caller_not_allowed",
        ),
        (
            {"proxy.pathsuffix": f"{MODEL_PATH.replace(PROJECT, 'someone-elses-project')}:generateContent"},
            "target_not_allowed",
        ),
        (
            {"proxy.pathsuffix": f"{MODEL_PATH.replace('us-central1', 'europe-west4')}:generateContent"},
            "target_not_allowed",
        ),
        (
            {"proxy.pathsuffix": f"{MODEL_PATH.replace('gemini-2.5-flash', 'imagen-4')}:generateContent"},
            "target_not_allowed",
        ),
        ({"proxy.pathsuffix": f"{MODEL_PATH}:predict"}, "unsupported_path"),
        (
            {"proxy.pathsuffix": f"/v1beta1/projects/{PROJECT}/locations/us-central1/endpoints/1:predict"},
            "unsupported_path",
        ),
        ({"proxy.pathsuffix": f"{MODEL_PATH}:generateContent/../../x"}, "unsupported_path"),
        (
            {"proxy.pathsuffix": MODEL_PATH.replace("us-central1", "evil.example.com#") + ":generateContent"},
            "unsupported_path",
        ),
        ({"request.verb": "GET"}, "unsupported_path"),
    ],
)
def test_authorize_fails_closed(overrides: dict[str, Any], reason: str) -> None:
    state = _authorize(**overrides)
    assert state["llmgw.denied.reason"] == reason
    if reason == "unsupported_path":
        assert "llm.target.url" not in state


def test_authorize_subject_principal_when_no_email() -> None:
    """Identities without an email claim (e.g. workload identities) are matched by subject."""
    state = _authorize(
        **{
            "jwt.VJ-Verify-Caller-Token.claim.email": None,
            "jwt.VJ-Verify-Caller-Token.claim.email_verified": None,
            "llmgw.allowed_callers": "112233445566778899",
        }
    )
    assert state["llmgw.denied.reason"] == ""
    assert state["llmgw.caller.principal"] == "112233445566778899"


def test_extract_tokens_gemini_generate_content_json() -> None:
    """Non-streaming generateContent: usageMetadata incl. cached and thinking tokens."""
    body = json.dumps(
        {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": "Julian Sterling qualifies."}]},
                    "finishReason": "STOP",
                }
            ],
            "modelVersion": "gemini-2.5-flash-001",
            "usageMetadata": {
                "promptTokenCount": 1200,
                "candidatesTokenCount": 80,
                "thoughtsTokenCount": 40,
                "cachedContentTokenCount": 1024,
                "totalTokenCount": 1320,
            },
        }
    )
    state = run_apigee_js(
        EXTRACT_TOKENS_JS, {"response.header.Content-Type": "application/json; charset=UTF-8", "response.content": body}
    )
    assert state["isFinal"] is True
    assert state["llm.model"] == "gemini-2.5-flash-001"
    assert state["llm.promptTokenCount"] == 1200
    assert state["llm.candidatesTokenCount"] == 80
    assert state["llm.thoughtsTokenCount"] == 40
    assert state["llm.cachedTokenCount"] == 1024
    assert state["llm.totalTokenCount"] == 1280


def test_extract_tokens_gemini_stream_generate_content_sse() -> None:
    """streamGenerateContent?alt=sse: one event per chunk; only the final chunk (finishReason) is final."""
    chunks = [
        'data: {"candidates":[{"content":{"role":"model","parts":[{"text":"Income "}]}}],'
        '"usageMetadata":{"promptTokenCount":300},"modelVersion":"gemini-2.5-flash-001"}\r\n\r\n',
        'data: {"candidates":[{"content":{"role":"model","parts":[{"text":"verified."}]},"finishReason":"STOP"}],'
        '"usageMetadata":{"promptTokenCount":300,"candidatesTokenCount":25,"thoughtsTokenCount":12,'
        '"totalTokenCount":337},"modelVersion":"gemini-2.5-flash-001"}\r\n\r\n',
    ]
    state: dict[str, Any] = {"response.header.Content-Type": "text/event-stream"}
    finals = []
    for chunk in chunks:
        state["response.event.current.content"] = chunk
        state = run_apigee_js(EXTRACT_TOKENS_JS, state)
        finals.append(state["isFinal"])
    assert finals == [False, True]
    assert state["llm.promptTokenCount"] == 300
    assert state["llm.candidatesTokenCount"] == 25
    assert state["llm.thoughtsTokenCount"] == 12
    assert state["llm.totalTokenCount"] == 325
    assert "llm.cachedTokenCount" not in state


def test_extract_tokens_copies_are_identical() -> None:
    copies = [
        REPO_ROOT / "apps/apigee/proxies/partner-agents/apiproxy/resources/jsc/extract-tokens.js",
        REPO_ROOT / "apps/apigee/sharedflows/usage-metering/sharedflowbundle/resources/jsc/extract-tokens.js",
        EXTRACT_TOKENS_JS,
    ]
    assert len({path.read_text() for path in copies}) == 1
