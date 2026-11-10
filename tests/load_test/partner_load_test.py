# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Locust load test THROUGH the Apigee partner front door (``make load-test-partner``).

Unlike ``load_test.py``, which calls the orchestrator on the Vertex AI API directly (and so never
touches Apigee), every request here goes to ``https://partner.<domain>`` with a partner API key, so
it shows up in Apigee Analytics, Debug sessions, the token quota and the usage-metering logs.

The traffic mix is the scenario catalog ``tests/partner/scenarios.py``: one weighted task per
scenario with ``weight > 0`` (the destructive quota-exhaustion scenario only with
``PARTNER_INCLUDE_QUOTA=1``). A request counts as a success when its status is in the scenario's
expected set, so the expected 401/403 rejections are successes, each under its own name. SSE
scenarios also report time to first event and total stream time.

``scripts/partner_load_test.sh`` runs this file from the private test VM, because the partner
host only resolves inside the VPC. Configuration (never logged):

* ``PARTNER_HOST``: ``partner.<domain>`` (TLS SNI and ``Host``)
* ``PARTNER_ENTRY_IP``: the entry point address the host resolves to (``curl --resolve``)
* ``PARTNER_API_KEY``: the partner consumer key
* ``PARTNER_CA_FILE``: PEM of the internal root CA
* ``PARTNER_INCLUDE_QUOTA``: ``1`` to add the quota-exhaustion scenario (burns the product quota)
* ``PARTNER_LOAD_RUN_ID``: optional id sent as ``x-esmeralda-load-run`` to find the run in logs
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from locust import HttpUser, between, events
from requests.adapters import HTTPAdapter

_HERE = os.path.dirname(os.path.abspath(__file__))
# In the repo the catalog lives in tests/partner/; on the test VM both files sit side by side.
for _candidate in (_HERE, os.path.join(_HERE, os.pardir, "partner")):
    if os.path.isfile(os.path.join(_candidate, "scenarios.py")) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)
from scenarios import Scenario, load_mix  # noqa: E402

RUN_ID = os.getenv("PARTNER_LOAD_RUN_ID") or uuid.uuid4().hex[:12]
CONNECT_TIMEOUT = 5.0


class ResolveAdapter(HTTPAdapter):
    """Connects to the URL's IP but uses ``hostname`` for TLS SNI and certificate checks."""

    def __init__(self, hostname: str, **kwargs: Any):
        self._hostname = hostname
        super().__init__(**kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["server_hostname"] = self._hostname
        kwargs["assert_hostname"] = self._hostname
        super().init_poolmanager(*args, **kwargs)


def config(env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ if env is None else env)
    missing = [k for k in ("PARTNER_HOST", "PARTNER_ENTRY_IP", "PARTNER_API_KEY", "PARTNER_CA_FILE") if not env.get(k)]
    if missing:
        raise RuntimeError(f"Missing environment variables for the partner load test: {', '.join(missing)}")
    return env


def include_quota(env: Optional[Dict[str, str]] = None) -> bool:
    return (os.environ if env is None else env).get("PARTNER_INCLUDE_QUOTA", "0") == "1"


def traceparent() -> str:
    return f"00-{uuid.uuid4().hex}-{uuid.uuid4().hex[:16]}-01"


def outcome(scenario: Scenario, status: int, text: str) -> Optional[str]:
    """None when the response counts as a success in the load test, else the failure reason."""
    if scenario.attempts > 1 and status == 200:
        return None  # quota exhaustion: 200 until the budget runs out, then the expected 429
    return scenario.check(status, text)


def _fire(user: Any, name: str, ms: float, length: int = 0) -> None:
    user.environment.events.request.fire(
        request_type="SSE", name=name, response_time=ms, response_length=length, exception=None, context={}
    )


def run_scenario(user: Any, scenario: Scenario) -> None:
    """Sends one request for ``scenario`` from ``user`` and records the outcome."""
    request_id = uuid.uuid4().hex
    path, headers, body = scenario.render(user.user_id, request_id)
    headers = {
        "Host": user.partner_host,
        "traceparent": traceparent(),
        "x-esmeralda-load-run": RUN_ID,
        "x-esmeralda-partner-user": user.user_id,
        **headers,
        **scenario.auth_headers(user.api_key),
    }
    started = time.monotonic()
    with user.client.request(
        scenario.method,
        path,
        data=body,
        headers=headers,
        name=scenario.name,
        stream=scenario.stream,
        catch_response=True,
        timeout=(CONNECT_TIMEOUT, scenario.timeout),
    ) as response:
        try:
            if scenario.stream and response.status_code == 200:
                parts, ttfe, size = [], None, 0
                for chunk in response.iter_content(chunk_size=None):
                    if not chunk:
                        continue
                    if ttfe is None:
                        ttfe = (time.monotonic() - started) * 1000
                        _fire(user, f"{scenario.name} [time to first event]", ttfe)
                        _fire(user, "ALL SSE [time to first event]", ttfe)
                    size += len(chunk)
                    parts.append(chunk)
                    if time.monotonic() - started > scenario.timeout:
                        raise TimeoutError(f"stream not complete after {scenario.timeout:.0f}s")
                text = b"".join(parts).decode("utf-8", "replace")
                total = (time.monotonic() - started) * 1000
                _fire(user, f"{scenario.name} [stream total]", total, size)
                _fire(user, "ALL SSE [stream total]", total, size)
            else:
                text = response.text
        except Exception as exc:  # stream interrupted mid-way
            response.failure(f"{scenario.name}: stream interrupted: {type(exc).__name__}")
            return
        problem = outcome(scenario, response.status_code, text)
        if problem:
            response.failure(f"{scenario.name}: {problem}")
        else:
            response.success()


def build_tasks(scenarios: Iterable[Scenario]) -> Dict[Callable[[Any], None], int]:
    """Locust ``tasks`` mapping: one weighted callable per scenario."""
    tasks: Dict[Callable[[Any], None], int] = {}
    for scenario in scenarios:

        def task(user: Any, _scenario: Scenario = scenario) -> None:
            run_scenario(user, _scenario)

        task.__name__ = scenario.name
        tasks[task] = scenario.weight
    return tasks


def task_summary(tasks: Any) -> Tuple[Tuple[str, int], ...]:
    """(name, weight) pairs of a ``tasks`` mapping, or of the list Locust expands it into."""
    if isinstance(tasks, dict):
        return tuple((fn.__name__, weight) for fn, weight in tasks.items())
    counts: Dict[str, int] = {}
    for fn in tasks:
        counts[fn.__name__] = counts.get(fn.__name__, 0) + 1
    return tuple(counts.items())


class PartnerUser(HttpUser):
    """A partner app calling the front door with its API key (plus deliberate bad calls)."""

    wait_time = between(5, 15)  # the specialist runs one instance: keep the pace gentle
    host = "https://{}".format(os.getenv("PARTNER_ENTRY_IP", "partner-entry-ip-not-set"))
    tasks = build_tasks(load_mix(include_destructive=include_quota()))

    def on_start(self) -> None:
        cfg = config()
        self.partner_host = cfg["PARTNER_HOST"]
        self.api_key = cfg["PARTNER_API_KEY"]
        self.user_id = f"partner-load-{uuid.uuid4().hex[:8]}"
        self.client.verify = cfg["PARTNER_CA_FILE"]
        self.client.mount("https://", ResolveAdapter(self.partner_host))


@events.test_start.add_listener
def _announce(environment: Any, **_: Any) -> None:
    mix = ", ".join(f"{name}={weight}" for name, weight in task_summary(PartnerUser.tasks))
    print(f"Partner load run {RUN_ID}: {mix}", flush=True)
