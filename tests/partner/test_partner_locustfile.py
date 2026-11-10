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

"""Offline checks of the partner Locust file's task builder (no network, no Locust run)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from partner_testlib import LOCUSTFILE, PARTNER_DIR, load

pytest.importorskip("locust")
sc = load("scenarios", PARTNER_DIR / "scenarios.py")
lt = load("partner_load_test", LOCUSTFILE)


class FakeResponse:
    def __init__(self, status: int, chunks: list[bytes]):
        self.status_code = status
        self._chunks = chunks
        self.text = b"".join(chunks).decode()
        self.result: str | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def iter_content(self, chunk_size=None):
        yield from self._chunks

    def success(self) -> None:
        self.result = "success"

    def failure(self, reason: str) -> None:
        self.result = f"failure: {reason}"


class FakeClient:
    def __init__(self, responses: dict[str, FakeResponse]):
        self.responses = responses
        self.calls: list[dict] = []

    def request(self, method, path, **kwargs):
        self.calls.append({"method": method, "path": path, **kwargs})
        return self.responses[kwargs["name"]]


def _user(responses: dict[str, FakeResponse]):
    fired: list[dict] = []
    events = SimpleNamespace(request=SimpleNamespace(fire=lambda **kw: fired.append(kw)))
    user = SimpleNamespace(
        client=FakeClient(responses),
        environment=SimpleNamespace(events=events),
        partner_host="partner.example.internal",
        api_key="secret-key",
        user_id="partner-load-test0001",
    )
    return user, fired


def test_tasks_follow_the_catalog_weights() -> None:
    tasks = lt.build_tasks(sc.load_mix())
    assert dict(lt.task_summary(tasks)) == {s.name: s.weight for s in sc.load_mix()}
    with_quota = dict(lt.task_summary(lt.build_tasks(sc.load_mix(include_destructive=True))))
    assert "orchestrator-quota-exhaustion" in with_quota
    assert "orchestrator-quota-exhaustion" not in dict(lt.task_summary(lt.PartnerUser.tasks))


def test_expected_rejection_counts_as_success_and_sends_tags() -> None:
    s = sc.by_name("agents-no-key")
    user, _ = _user({s.name: FakeResponse(401, [b'{"fault":"x"}'])})
    lt.run_scenario(user, s)
    assert user.client.responses[s.name].result == "success"
    call = user.client.calls[0]
    assert call["headers"]["Host"] == "partner.example.internal"
    assert "x-api-key" not in call["headers"]
    assert call["headers"]["x-esmeralda-partner-user"] == "partner-load-test0001"
    assert call["headers"]["traceparent"].startswith("00-")
    assert call["name"] == s.name and call["stream"] is False


def test_unexpected_status_is_a_failure() -> None:
    s = sc.by_name("agents-specialist-card-blocked")
    user, _ = _user({s.name: FakeResponse(200, [b"{}"])})
    lt.run_scenario(user, s)
    assert user.client.responses[s.name].result.startswith("failure: agents-specialist-card-blocked: HTTP 200")


def test_sse_records_time_to_first_event_and_total() -> None:
    s = sc.by_name("orchestrator-income")
    user, fired = _user({s.name: FakeResponse(200, [b"data: thinking\n\n", b"data: Julian Sterling\n\n"])})
    lt.run_scenario(user, s)
    assert user.client.responses[s.name].result == "success"
    assert user.client.calls[0]["headers"]["x-api-key"] == "secret-key"
    assert user.client.calls[0]["stream"] is True
    names = [f["name"] for f in fired]
    assert names == [
        "orchestrator-income [time to first event]",
        "ALL SSE [time to first event]",
        "orchestrator-income [stream total]",
        "ALL SSE [stream total]",
    ]
    assert all(f["request_type"] == "SSE" for f in fired)


def test_sse_without_the_answer_is_a_failure() -> None:
    s = sc.by_name("orchestrator-income")
    user, _ = _user({s.name: FakeResponse(200, [b"data: error\n\n"])})
    lt.run_scenario(user, s)
    assert "without 'Sterling'" in user.client.responses[s.name].result


def test_quota_scenario_accepts_200_until_429() -> None:
    s = sc.by_name("orchestrator-quota-exhaustion")
    assert lt.outcome(s, 200, "{}") is None
    assert lt.outcome(s, 429, "") is None
    assert lt.outcome(s, 500, "") is not None


def test_resolve_adapter_pins_sni_and_hostname() -> None:
    adapter = lt.ResolveAdapter("partner.example.internal")
    kw = adapter.poolmanager.connection_pool_kw
    assert kw["server_hostname"] == "partner.example.internal"
    assert kw["assert_hostname"] == "partner.example.internal"


def test_config_requires_all_settings() -> None:
    with pytest.raises(RuntimeError, match="PARTNER_API_KEY"):
        lt.config({"PARTNER_HOST": "h", "PARTNER_ENTRY_IP": "i", "PARTNER_CA_FILE": "c"})
