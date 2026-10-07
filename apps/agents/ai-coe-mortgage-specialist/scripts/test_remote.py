# Copyright 2025 Google LLC
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

"""Remote smoke test for the deployed A2A specialist (Agent Engine).

Prints a readable summary (agent card, tool trace, final answer, ids, timings).
Set TEST_VERBOSE=1 to also dump the raw SDK objects for debugging.
"""

import asyncio
import json
import os
import sys
import time
import traceback
import uuid
from dotenv import load_dotenv

# Ensure the current directory is in PYTHONPATH so we can import 'agent_app' if needed
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Load environment variables
load_dotenv()

# --- CRITICAL: Set environment variables BEFORE Gemini/Vertex SDK instantiation ---
os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

import vertexai
from google.genai import types

VERBOSE = os.getenv("TEST_VERBOSE", "").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Console helpers (plain ANSI; colors disabled when not a TTY or NO_COLOR set)
# ---------------------------------------------------------------------------
_COLOR = sys.stdout.isatty() and not os.getenv("NO_COLOR")


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(t): return _c("1", t)
def dim(t): return _c("2", t)
def green(t): return _c("32", t)
def red(t): return _c("31", t)
def yellow(t): return _c("33", t)
def cyan(t): return _c("36", t)


WIDTH = 78


def section(title: str) -> None:
    print()
    print(cyan("━" * WIDTH))
    print(cyan(bold(f"  {title}")))
    print(cyan("━" * WIDTH))


def kv(key: str, value, indent: int = 2) -> None:
    print(f"{' ' * indent}{dim(f'{key:<14}')} {value}")


def block(text: str, indent: int = 2) -> None:
    pad = " " * indent
    print(dim(pad + "┌" + "─" * (WIDTH - indent - 1)))
    for line in str(text).splitlines() or [""]:
        print(f"{dim(pad + '│')} {line}")
    print(dim(pad + "└" + "─" * (WIDTH - indent - 1)))


def short(value, limit: int = 160) -> str:
    s = value if isinstance(value, str) else json.dumps(value, default=str)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def raw_dump(label: str, obj) -> None:
    if VERBOSE:
        print(dim(f"\n  [raw {label}]"))
        print(dim(f"  {obj!r}"))


def fail(msg: str, exc: BaseException | None = None) -> None:
    print()
    print(red(bold(f"❌ {msg}")))
    if exc is not None:
        print(red(f"   {type(exc).__name__}: {exc}"))
        print(dim("".join(traceback.format_exception(exc))))
    sys.exit(1)


# ---------------------------------------------------------------------------
# A2A response helpers
# ---------------------------------------------------------------------------
def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _unwrap_task(resp):
    """on_message_send returns a list of (Task, update) tuples: use the last task."""
    while isinstance(resp, (list, tuple)) and resp:
        resp = resp[-1] if isinstance(resp, list) else resp[0]
    if isinstance(resp, dict) and "result" in resp:
        resp = resp["result"]
    return resp


def _task_state(task):
    """Best-effort extraction of the A2A task state from dict or SDK object responses."""
    try:
        state = _get(_get(task, "status") or {}, "state")
        return getattr(state, "value", state)
    except Exception:
        return None


def _parts(container):
    for part in _get(container, "parts") or []:
        yield _get(part, "root", part)


def _texts(container):
    return [t for p in _parts(container) if (t := _get(p, "text"))]


def _print_agent_card(card) -> None:
    kv("name", bold(str(_get(card, "name"))))
    kv("description", _get(card, "description"))
    kv("version", _get(card, "version"))
    kv("protocol", _get(card, "protocol_version"))
    kv("transport", _get(card, "preferred_transport"))
    kv("url", _get(card, "url"))
    kv("input modes", ", ".join(_get(card, "default_input_modes") or []))
    kv("output modes", ", ".join(_get(card, "default_output_modes") or []))
    skills = _get(card, "skills") or []
    kv("skills", f"{len(skills)}")
    for s in skills:
        tags = ", ".join(_get(s, "tags") or [])
        skill_id = dim("(" + str(_get(s, "id")) + ")")
        tag_str = dim("  [" + tags + "]") if tags else ""
        print(f"      • {bold(str(_get(s, 'name')))} {skill_id}{tag_str}")
        print(f"        {dim(str(_get(s, 'description')))}")


def _print_tool_trace(task) -> int:
    """Prints function calls/responses found in the task history. Returns tool error count."""
    errors = 0
    calls = 0
    for msg in _get(task, "history") or []:
        for p in _parts(msg):
            data = _get(p, "data")
            meta = _get(p, "metadata") or {}
            kind = meta.get("adk_type") if isinstance(meta, dict) else None
            if not isinstance(data, dict) or kind not in ("function_call", "function_response"):
                continue
            if kind == "function_call":
                calls += 1
                print(f"  {yellow('→')} {bold(str(data.get('name')))}"
                      f"{dim('(' + short(data.get('args', {}), 120) + ')')}")
            else:
                resp = data.get("response") or {}
                is_err = bool(resp.get("isError")) if isinstance(resp, dict) else False
                errors += is_err
                mark = red("✗ error") if is_err else green("✓ ok")
                body = resp
                if isinstance(resp, dict) and resp.get("content"):
                    body = " ".join(_get(c, "text", "") for c in resp["content"] if isinstance(c, dict))
                print(f"  {dim('←')} {data.get('name')} {mark}  {dim(short(body, 110))}")
    if calls == 0:
        print(dim("  (no tool calls recorded in task history)"))
    return errors


async def main(user_input: str):
    PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION") or "us-central1"
    RESOURCE_ID = os.getenv("REASONING_ENGINE_ID") or os.getenv("RESOURCE_ID")
    if not PROJECT_ID or not RESOURCE_ID:
        raise SystemExit("❌ Set GOOGLE_CLOUD_PROJECT and REASONING_ENGINE_ID (make test-ai-coe-mortgage-specialist-remote resolves them from Terragrunt outputs).")
    RESOURCE_NAME = f"projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{RESOURCE_ID}"
    t_start = time.monotonic()

    section("🧪 A2A specialist remote test")
    kv("project", PROJECT_ID)
    kv("location", LOCATION)
    kv("engine", RESOURCE_ID)
    kv("verbose", "on" if VERBOSE else dim("off (TEST_VERBOSE=1 for raw dumps)"))

    try:
        client = vertexai.Client(
            project=PROJECT_ID,
            location=LOCATION,
            http_options=types.HttpOptions(api_version="v1beta1"),
        )
        remote_agent = client.agent_engines.get(name=RESOURCE_NAME)
    except Exception as e:
        fail(f"Could not load Agent Engine {RESOURCE_NAME}", e)
    kv("status", green("✓ engine resolved"))

    override_url = os.getenv("AGENT_URL")
    if override_url and hasattr(remote_agent, "agent_card"):
        kv("card url", f"overridden → {override_url}")
        remote_agent.agent_card.url = override_url

    # 1. Agent card
    section("📇 1. Authenticated agent card")
    t0 = time.monotonic()
    try:
        card = await remote_agent.handle_authenticated_agent_card()
        _print_agent_card(card)
        kv("elapsed", f"{time.monotonic() - t0:.1f}s")
        raw_dump("agent card", card)
    except Exception as e:
        # Non-fatal: the message round-trip below is the real check.
        print(yellow(f"  ⚠️  Card fetch failed: {type(e).__name__}: {e}"))
        print(dim("".join(traceback.format_exception(e))))

    # 2. Message round-trip
    section("💬 2. on_message_send")
    message_id = f"remote-test-{uuid.uuid4()}"
    kv("message id", message_id)
    kv("query", bold(user_input))
    t0 = time.monotonic()
    try:
        response = await remote_agent.on_message_send(
            messageId=message_id,
            role="user",
            parts=[{"kind": "text", "text": user_input}],
        )
    except Exception as e:
        fail("on_message_send raised an exception", e)
    elapsed = time.monotonic() - t0
    raw_dump("response", response)

    task = _unwrap_task(response)
    state = _task_state(task)
    status_msg = _get(_get(task, "status") or {}, "message")

    print()
    kv("task id", _get(task, "id"))
    kv("context id", _get(task, "context_id"))
    kv("state", green(str(state)) if str(state).lower().endswith("completed") else red(str(state)))
    kv("elapsed", f"{elapsed:.1f}s")
    metadata = _get(task, "metadata") or {}
    usage = metadata.get("adk_usage_metadata") if isinstance(metadata, dict) else None
    if isinstance(usage, dict):
        def _n(k): return int(usage.get(k) or 0)
        kv("tokens", f"prompt={_n('promptTokenCount')}  output={_n('candidatesTokenCount')}  "
                     f"thoughts={_n('thoughtsTokenCount')}  total={_n('totalTokenCount')}")

    print(f"\n  {bold('Tool trace')}")
    tool_errors = _print_tool_trace(task)

    answer = "\n\n".join(t for a in (_get(task, "artifacts") or []) for t in _texts(a))
    print(f"\n  {bold('Answer')}")
    if answer:
        block(answer)
    else:
        print(yellow("  (no text artifacts in the task)"))

    if status_msg is not None and _texts(status_msg):
        print(f"\n  {bold('Status message')}")
        block("\n".join(_texts(status_msg)))

    # Verdict
    section("📋 Result")
    if state is None:
        print(yellow("  ⚠️  Could not determine the A2A task state; dumping raw response."))
        print(dim(f"  {response!r}"))
        fail("Unknown A2A task state.")
    if not str(state).lower().endswith("completed"):
        if not VERBOSE:
            print(dim(f"  raw response: {response!r}"))
        fail(f"A2A task finished in state '{state}' (expected completed).")
    if not answer:
        fail("A2A task completed but returned no text answer.")
    if tool_errors:
        print(yellow(f"  ⚠️  {tool_errors} tool call(s) returned isError=true (see trace above)."))
    print(green(bold(f"  ✅ Specialist test PASSED in {time.monotonic() - t_start:.1f}s")))


if __name__ == "__main__":
    test_query = sys.argv[1] if len(sys.argv) > 1 else "I'm reviewing the Rivera family's $700K loan. Can you summarize their 2024 tax returns?"
    asyncio.run(main(test_query))
