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

"""Offline unit tests for apps/apigee/.../extract-tokens.js across SSE and JSON dialects."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACT_TOKENS_JS = (
    REPO_ROOT
    / "apps"
    / "apigee"
    / "sharedflows"
    / "usage-metering"
    / "sharedflowbundle"
    / "resources"
    / "jsc"
    / "extract-tokens.js"
)


def _find_node_binary() -> str | None:
    """Finds a usable Node.js binary on PATH or standard user install locations."""
    found = shutil.which("node") or shutil.which("nodejs")
    if found:
        return found
    nvm_dir = Path.home() / ".nvm" / "versions" / "node"
    if nvm_dir.is_dir():
        candidates = sorted(nvm_dir.glob("*/bin/node"), reverse=True)
        if candidates:
            return str(candidates[0])
    return None


def _run_apigee_js(script_path: Path, initial_vars: dict[str, Any]) -> dict[str, Any]:
    """Executes an Apigee JavaScript resource against a mocked `context` object."""
    node_bin = _find_node_binary()
    assert node_bin is not None, "Node.js binary is required to run Apigee JS policy tests"

    harness = """
const fs = require('fs');
const store = Object.assign({}, JSON.parse(process.env.INITIAL_VARS || '{}'));
global.context = {
  getVariable: function(name) {
    return Object.prototype.hasOwnProperty.call(store, name) ? store[name] : null;
  },
  setVariable: function(name, value) {
    store[name] = value;
  }
};
const code = fs.readFileSync(process.env.SCRIPT_PATH, 'utf8');
eval(code);
process.stdout.write(JSON.stringify(store));
"""
    proc = subprocess.run(
        [node_bin, "-e", harness],
        env={
            "INITIAL_VARS": json.dumps(initial_vars),
            "SCRIPT_PATH": str(script_path),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def test_adk_stream_query_sse_event_stream() -> None:
    """Verifies ADK :streamQuery multi-chunk SSE stream extracts prompt, candidates, and total tokens."""
    chunks = [
        'data: {"content":{"parts":[{"text":"Checking Julian Sterling\'s income..."}]},"partial":true}\n\n',
        'data: {"content":{"parts":[{"text":" Income verified at $185,000."}]},"partial":false,'
        '"model_version":"gemini-2.5-flash",'
        '"usage_metadata":{"prompt_token_count":142,"candidates_token_count":58,"total_token_count":200}}\n\n',
    ]

    state: dict[str, Any] = {"response.header.Content-Type": "text/event-stream"}
    for idx, chunk in enumerate(chunks):
        state["response.event.current.content"] = chunk
        state = _run_apigee_js(EXTRACT_TOKENS_JS, state)
        if idx == 0:
            assert state["isFinal"] is False

    assert state["isFinal"] is True
    assert state["llm.model"] == "gemini-2.5-flash"
    assert state["llm.promptTokenCount"] == 142
    assert state["llm.candidatesTokenCount"] == 58
    assert state["llm.totalTokenCount"] == state["llm.promptTokenCount"] + state["llm.candidatesTokenCount"]
    assert state["llm.totalTokenCount"] == 200


def test_a2a_nested_message_stream_sse() -> None:
    """Verifies A2A message/stream nested result.status.message.usage_metadata extraction."""
    sse_event = (
        'data: {"jsonrpc":"2.0","id":"1","result":{"status":{"state":"completed",'
        '"message":{"metadata":{"model":"gemini-2.5-pro","usage_metadata":'
        '{"prompt_token_count":310,"candidates_token_count":90}}}}}}\n\n'
    )
    state = _run_apigee_js(
        EXTRACT_TOKENS_JS,
        {
            "response.header.Content-Type": "text/event-stream",
            "response.event.current.content": sse_event,
        },
    )
    assert state["isFinal"] is True
    assert state["llm.model"] == "gemini-2.5-pro"
    assert state["llm.promptTokenCount"] == 310
    assert state["llm.candidatesTokenCount"] == 90
    assert state["llm.totalTokenCount"] == state["llm.promptTokenCount"] + state["llm.candidatesTokenCount"]
    assert state["llm.totalTokenCount"] == 400


def test_vertex_ai_gemini_sse_event_stream() -> None:
    """Verifies Vertex AI Gemini SSE stream (`usageMetadata`) sets total == prompt + candidates."""
    chunks = [
        'data: {"candidates":[{"content":{"parts":[{"text":"Part 1"}]}}],'
        '"usageMetadata":{"promptTokenCount":85,"candidatesTokenCount":12,"totalTokenCount":97}}\n\n',
        'data: {"candidates":[{"content":{"parts":[{"text":"Part 2"}]},"finishReason":"STOP"}],'
        '"modelVersion":"gemini-2.5-flash",'
        '"usageMetadata":{"promptTokenCount":85,"candidatesTokenCount":45,"totalTokenCount":130}}\n\n',
    ]

    state: dict[str, Any] = {"response.header.Content-Type": "text/event-stream"}
    for idx, chunk in enumerate(chunks):
        state["response.event.current.content"] = chunk
        state = _run_apigee_js(EXTRACT_TOKENS_JS, state)
        if idx == 0:
            assert state["isFinal"] is False

    assert state["isFinal"] is True
    assert state["llm.model"] == "gemini-2.5-flash"
    assert state["llm.promptTokenCount"] == 85
    assert state["llm.candidatesTokenCount"] == 45
    assert state["llm.totalTokenCount"] == state["llm.promptTokenCount"] + state["llm.candidatesTokenCount"]
    assert state["llm.totalTokenCount"] == 130


def test_anthropic_sse_event_stream() -> None:
    """Verifies Anthropic message_start + message_delta SSE stream accumulates input + output tokens."""
    start_chunk = (
        'event: message_start\n'
        'data: {"type":"message_start","message":{"model":"claude-sonnet-4-20250514",'
        '"usage":{"input_tokens":215,"output_tokens":1}}}\n\n'
    )
    delta_chunk = (
        'event: message_delta\n'
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        '"usage":{"output_tokens":65}}\n\n'
    )

    state: dict[str, Any] = {
        "response.header.Content-Type": "text/event-stream",
        "response.event.current.content": start_chunk,
    }
    state = _run_apigee_js(EXTRACT_TOKENS_JS, state)
    assert state["isFinal"] is False
    assert state["llm.promptTokenCount"] == 215

    state["response.event.current.content"] = delta_chunk
    state = _run_apigee_js(EXTRACT_TOKENS_JS, state)
    assert state["isFinal"] is True
    assert state["llm.model"] == "claude-sonnet-4-20250514"
    assert state["llm.promptTokenCount"] == 215
    assert state["llm.candidatesTokenCount"] == 65
    assert state["llm.totalTokenCount"] == state["llm.promptTokenCount"] + state["llm.candidatesTokenCount"]
    assert state["llm.totalTokenCount"] == 280


def test_non_streaming_json_response() -> None:
    """Verifies non-streaming application/json response parses usage metadata cleanly."""
    json_body = json.dumps(
        {
            "model_version": "gemini-2.5-flash",
            "output": "Applicant Julian Sterling qualifies for the prime rate.",
            "usage_metadata": {
                "prompt_token_count": 120,
                "candidates_token_count": 34,
                "total_token_count": 154,
            },
        }
    )
    state = _run_apigee_js(
        EXTRACT_TOKENS_JS,
        {
            "response.header.Content-Type": "application/json",
            "response.content": json_body,
        },
    )
    assert state["isFinal"] is True
    assert state["llm.model"] == "gemini-2.5-flash"
    assert state["llm.promptTokenCount"] == 120
    assert state["llm.candidatesTokenCount"] == 34
    assert state["llm.totalTokenCount"] == state["llm.promptTokenCount"] + state["llm.candidatesTokenCount"]
    assert state["llm.totalTokenCount"] == 154
