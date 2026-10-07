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

"""Remote smoke test for the deployed ADK orchestrator (Agent Engine streamQuery).

Prints a readable event trace (authors, tool calls, A2A hand-offs, token usage),
the final answer, ids and timings. Set TEST_VERBOSE=1 to also dump every raw
stream event for debugging.
"""

import asyncio
import os
import sys
import json
import time
import traceback
import httpx
import google.auth
import google.auth.transport.requests
from dotenv import load_dotenv

# Ensure current directory is in PYTHONPATH
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv()

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
def magenta(t): return _c("35", t)


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


def fail(msg: str, exc: BaseException | None = None, details: str | None = None) -> None:
    print()
    print(red(bold(f"❌ {msg}")))
    if details:
        block(details)
    if exc is not None:
        print(red(f"   {type(exc).__name__}: {exc}"))
        print(dim("".join(traceback.format_exception(exc))))
    sys.exit(1)


def get_gcp_access_token() -> str:
    """Retrieves a Google Cloud OAuth2 access token natively."""
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        auth_request = google.auth.transport.requests.Request()
        credentials.refresh(auth_request)
        if credentials.token:
            return credentials.token
    except Exception as e:
        print(yellow(f"  ⚠️  Native credential resolution failed: {e}"))

    # Fallback to gcloud CLI
    import subprocess
    try:
        return subprocess.check_output(
            ["gcloud", "auth", "print-access-token"],
            text=True
        ).strip()
    except Exception as ex:
        print(red(f"  gcloud CLI fallback failed: {ex}"))
        raise RuntimeError("No valid GCP credentials found.")


class StreamReport:
    """Renders ADK stream events as a compact trace and collects stats."""

    def __init__(self):
        self.events = 0
        self.errors: list = []
        self.tool_calls = 0
        self.tool_errors = 0
        self.authors: list[str] = []
        self.final_text: dict[str, str] = {}  # author -> last full text
        self.tokens = {"prompt": 0, "candidates": 0, "total": 0}
        self.unparsed: list[str] = []

    def _author(self, ev: dict) -> str:
        author = ev.get("author") or "?"
        if author not in self.authors:
            self.authors.append(author)
        return author

    def handle(self, ev, elapsed: float) -> None:
        self.events += 1
        ts = dim(f"[{elapsed:6.1f}s]")
        if VERBOSE:
            print(dim(f"  [raw event] {json.dumps(ev, default=str)}"))
        if not isinstance(ev, dict):
            self.unparsed.append(str(ev))
            print(f"  {ts} {dim(short(str(ev)))}")
            return

        if "error" in ev or "error_code" in ev:
            self.errors.append(ev)
            print(f"  {ts} {red('✗ stream error')} {short(ev, 300)}")
            return

        usage = ev.get("usage_metadata") or {}
        if usage and not ev.get("partial"):
            self.tokens["prompt"] += usage.get("prompt_token_count") or 0
            self.tokens["candidates"] += usage.get("candidates_token_count") or 0
            self.tokens["total"] += usage.get("total_token_count") or 0

        content = ev.get("content")
        if not (isinstance(content, dict) and content.get("parts")):
            if "output" in ev:
                self.final_text["output"] = self.final_text.get("output", "") + str(ev["output"])
                print(f"  {ts} {dim('output')} {short(ev['output'])}")
            else:
                print(f"  {ts} {dim('event')} {short(ev, 200)}")
            return

        author = self._author(ev)
        who = magenta(f"{author:<28}")
        for part in content["parts"]:
            if not isinstance(part, dict):
                continue
            if part.get("function_call"):
                fc = part["function_call"]
                self.tool_calls += 1
                name = fc.get("name")
                args = fc.get("args") or {}
                if name == "transfer_to_agent":
                    print(f"  {ts} {who} {yellow('⇢ transfer_to_agent')} → {bold(str(args.get('agent_name')))}")
                else:
                    print(f"  {ts} {who} {yellow('→ ' + str(name))}{dim('(' + short(args, 100) + ')')}")
            elif part.get("function_response"):
                fr = part["function_response"]
                resp = fr.get("response")
                is_err = isinstance(resp, dict) and bool(resp.get("isError") or resp.get("error"))
                self.tool_errors += is_err
                mark = red("✗ error") if is_err else green("✓")
                print(f"  {ts} {who} {dim('← ' + str(fr.get('name')))} {mark} {dim(short(resp, 90))}")
            elif part.get("text"):
                if part.get("thought"):
                    print(f"  {ts} {who} {dim('💭 ' + short(part['text'], 100))}")
                    continue
                if ev.get("partial"):
                    continue  # the final non-partial event carries the full text
                self.final_text[author] = part["text"]
                n = len(part["text"])
                print(f"  {ts} {who} {green('💬 text')} {dim(f'({n} chars)')} {dim(short(part['text'], 70))}")


async def main(user_input: str):
    PROJECT_ID = os.getenv("CX_AGENTS_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    RESOURCE_ID = os.getenv("ROOT_REASONING_ENGINE_ID") or os.getenv("REASONING_ENGINE_ID")
    if not PROJECT_ID or not RESOURCE_ID:
        raise SystemExit("❌ Set CX_AGENTS_PROJECT_ID and ROOT_REASONING_ENGINE_ID (make test-cx-mortgage-orchestrator-remote resolves them from Terragrunt outputs).")

    base_url = f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{RESOURCE_ID}"
    stream_url = f"{base_url}:streamQuery?alt=sse"
    user_id = "test-user-123"
    t_start = time.monotonic()

    section("🧪 ADK orchestrator remote test")
    kv("project", PROJECT_ID)
    kv("location", LOCATION)
    kv("engine", RESOURCE_ID)
    kv("verbose", "on" if VERBOSE else dim("off (TEST_VERBOSE=1 for raw events)"))

    try:
        token = get_gcp_access_token()
    except Exception as e:
        fail("Auth error", e)
    kv("auth", green("✓ access token acquired"))

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # 1. Session
    section("📝 1. Create session (Vertex AI Sessions API)")
    sessions_api_url = f"{base_url}/sessions"
    session_id = None
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(sessions_api_url, json={"user_id": user_id}, headers=headers)
    except Exception as e:
        fail("Session creation request failed", e)
    if resp.status_code != 200:
        fail(f"Session creation failed: HTTP {resp.status_code}", details=resp.text)
    operation_name = resp.json().get("name", "")
    parts = operation_name.split("/")
    session_id = parts[parts.index("sessions") + 1] if "sessions" in parts else parts[-1]
    if not session_id:
        fail("Server did not return a valid session ID.", details=resp.text)
    kv("user id", user_id)
    kv("session id", session_id)
    kv("elapsed", f"{time.monotonic() - t0:.1f}s")

    # The agent runtime uses VertexAiSessionService, which reads this same managed
    # session directly; no separate in-runtime registration step is needed.

    query_payload = {
        "class_method": "async_stream_query",
        "input": {
            "message": user_input,
            "user_id": user_id,
            "session_id": session_id
        }
    }

    # 2. Stream query
    section("📡 2. Stream query")
    kv("url", dim(stream_url))
    kv("query", bold(user_input))
    print()

    report = StreamReport()
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", stream_url, json=query_payload, headers=headers) as response:
                if response.status_code != 200:
                    await response.aread()
                    fail(f"streamQuery failed: HTTP {response.status_code}", details=response.text)

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data_str = line[5:].strip() if line.startswith("data:") else line.strip()
                    try:
                        ev = json.loads(data_str)
                    except json.JSONDecodeError:
                        ev = data_str
                    report.handle(ev, time.monotonic() - t0)
    except SystemExit:
        raise
    except Exception as e:
        fail(f"Error during stream after {report.events} event(s)", e)
    stream_elapsed = time.monotonic() - t0

    # Answer: prefer the last author that produced text (the delegated agent's reply).
    answer_author, answer = (None, "")
    for author in reversed(report.authors + ["output"]):
        if report.final_text.get(author):
            answer_author, answer = author, report.final_text[author]
            break

    section("🤖 Answer" + (f" (from {answer_author})" if answer_author else ""))
    if answer:
        block(answer)
    else:
        print(yellow("  (no text in the stream)"))
        if report.unparsed:
            print(dim("  unparsed stream lines:"))
            block("\n".join(report.unparsed))

    section("📋 Result")
    kv("session id", session_id)
    kv("events", report.events)
    kv("agents", " → ".join(report.authors) or "-")
    kv("tool calls", f"{report.tool_calls}" + (red(f" ({report.tool_errors} errored)") if report.tool_errors else ""))
    kv("tokens", f"prompt={report.tokens['prompt']}  output={report.tokens['candidates']}  total={report.tokens['total']}"
                 + dim("  (orchestrator only; remote A2A agent usage is reported by its own test)"))
    kv("stream time", f"{stream_elapsed:.1f}s")
    kv("total time", f"{time.monotonic() - t_start:.1f}s")

    if report.errors:
        fail(f"The agent stream reported {len(report.errors)} error event(s).",
             details="\n".join(json.dumps(e, indent=2, default=str) for e in report.errors))
    if not answer:
        fail("The agent returned no text response.")
    if report.tool_errors:
        print(yellow(f"  ⚠️  {report.tool_errors} tool call(s) returned an error (see trace above)."))
    print(green(bold("  ✅ Orchestrator test PASSED")))


if __name__ == "__main__":
    test_query = sys.argv[1] if len(sys.argv) > 1 else "Can you search documents for Julian Sterling with document_type tax_return?"
    asyncio.run(main(test_query))
