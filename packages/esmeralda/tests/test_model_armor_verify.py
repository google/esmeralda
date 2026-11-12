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

"""``esmeralda verify --model-armor`` (esmeralda.ops.model_armor), offline."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from esmeralda.ops import model_armor as ma
from esmeralda.ops import repo, runtime


def _cfg(**over):
    return {
        "model_calls": "off",
        "model_calls_sdp": False,
        "gateway": "off",
        "gateway_fail_open": True,
        "gateway_timeout_ms": 10000,
        "agent_project_ids": ["esm-dev-cx-agents-mock"],
        "region": "us-central1",
        **over,
    }


def _patch_outputs(monkeypatch, cfg):
    def fake_output(root, env, unit, name):
        if (unit, name) == (ma.GOVERNANCE_UNIT, "model_armor"):
            return cfg
        if (unit, name) == (ma.PROJECTS_UNIT, "governance_project_id"):
            return "esm-dev-governance-mock"
        return None

    monkeypatch.setattr(repo, "output", fake_output)


def _patch_query(monkeypatch, seconds=75.0, passed=True):
    calls = []

    async def fake_query(root, env, agent, message, *, timeout=300.0, session=None):
        calls.append(agent)
        return {"agent": agent, "passed": passed, "seconds": seconds, "tool_calls": ["search_documents"]}

    monkeypatch.setattr(runtime, "query_agent", fake_query)
    return calls


def test_blocked_reason_reads_prompt_feedback_finish_reason_and_errors():
    feedback = SimpleNamespace(block_reason=SimpleNamespace(name="MODEL_ARMOR"), block_reason_message="PI")
    assert "MODEL_ARMOR" in ma.blocked_reason(SimpleNamespace(prompt_feedback=feedback, candidates=[]))

    def answer(finish):
        candidate = SimpleNamespace(finish_reason=SimpleNamespace(name=finish))
        return SimpleNamespace(prompt_feedback=None, candidates=[candidate])

    assert ma.blocked_reason(answer("SAFETY")) == "finish_reason=SAFETY"
    assert ma.blocked_reason(answer("STOP")) is None
    assert ma.blocked_reason(error=RuntimeError("400 Blocked by Model Armor Floor Setting")) is not None
    assert ma.blocked_reason(error=RuntimeError("503 backend unavailable")) is None


def test_everything_off_only_runs_the_long_call(monkeypatch, tmp_path: Path):
    _patch_outputs(monkeypatch, _cfg())
    calls = _patch_query(monkeypatch)
    result = asyncio.run(runtime.verify(tmp_path, "dev", model_armor=True))
    assert result["passed"] is True
    steps = {s["step"]: s for s in result["steps"]}
    assert steps["model-armor:model-calls"]["skipped"] is True
    assert steps["model-armor:gateway"]["skipped"] is True
    assert steps["model-armor:long-call"]["exceeded_60s"] is True
    assert calls == [ma.LONG_CALL_AGENT]


def test_missing_layer4_output_fails(monkeypatch, tmp_path: Path):
    _patch_outputs(monkeypatch, None)
    result = asyncio.run(ma.verify_model_armor(tmp_path, "dev"))
    assert result["passed"] is False
    assert "apply layer 4" in result["steps"][0]["detail"]


@pytest.mark.parametrize(
    ("mode", "blocked", "expected"),
    [
        ("enforce", "prompt_feedback.block_reason=MODEL_ARMOR", True),
        ("enforce", None, False),
        ("inspect", None, True),
        ("inspect", "finish_reason=SAFETY", False),
    ],
)
def test_model_calls_expectation_follows_the_mode(monkeypatch, tmp_path: Path, mode, blocked, expected):
    monkeypatch.setattr(ma, "model_name", lambda root, agent=ma.MODEL_AGENT: "gemini-mock")
    seen = []

    def fake_calls(project, region, model):
        seen.append((project, region, model))
        return {"generateContent": blocked, "streamGenerateContent": blocked}

    monkeypatch.setattr(ma, "_gemini_calls", fake_calls)
    step = ma.check_model_calls(tmp_path, _cfg(model_calls=mode))
    assert step["passed"] is expected
    assert seen == [("esm-dev-cx-agents-mock", "us-central1", "gemini-mock")]


def test_gateway_on_needs_sanitize_log_entries(monkeypatch, tmp_path: Path):
    _patch_outputs(monkeypatch, _cfg(gateway="on"))
    _patch_query(monkeypatch)
    monkeypatch.setattr(ma, "_sanitize_entries", lambda root, project, start, timeout: [{"payload": {}}])
    step = asyncio.run(ma.check_gateway(tmp_path, "dev", _cfg(gateway="on"), timeout=10))
    assert step["passed"] is True and "1 Model Armor sanitize" in step["detail"]


def test_short_long_call_passes_but_says_it_was_not_exercised(monkeypatch, tmp_path: Path):
    _patch_query(monkeypatch, seconds=20.0)
    step = asyncio.run(ma.check_long_call(tmp_path, "dev", timeout=10))
    assert step["passed"] is True
    assert step["exceeded_60s"] is False and "not exercised" in step["detail"]
