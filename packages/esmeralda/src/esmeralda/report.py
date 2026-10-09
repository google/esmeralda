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

"""Query report: one rendering and one verdict for ADK and A2A agents, on every target.

ADK agents stream events (agent author, text, function calls and responses, token usage). A2A
agents return a task (state, history with function calls and responses, artifacts). Both are
reduced to the same report: a live trace, the answer, tool and token statistics, and a verdict.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any, TextIO

_COMPLETED_STATES = {"completed", "taskstate.completed", "task_state_completed"}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _is_tool_error(response: Any) -> bool:
    return isinstance(response, dict) and bool(response.get("isError") or response.get("error"))


@dataclass
class Report:
    stream: TextIO = field(default_factory=lambda: sys.stdout)
    verbose: bool = False
    color: bool | None = None
    started: float = field(default_factory=time.monotonic)

    events: int = 0
    errors: list[Any] = field(default_factory=list)
    tool_calls: int = 0
    tool_errors: int = 0
    authors: list[str] = field(default_factory=list)
    answer_author: str | None = None
    answer: str = ""
    tokens: dict[str, int] = field(default_factory=lambda: {"prompt": 0, "output": 0, "total": 0})
    task_state: str | None = None

    def __post_init__(self) -> None:
        if self.color is None:
            self.color = hasattr(self.stream, "isatty") and self.stream.isatty()

    # ---- output helpers -------------------------------------------------------------------

    def _c(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def line(self, text: str = "") -> None:
        print(text, file=self.stream, flush=True)

    def section(self, title: str) -> None:
        self.line()
        self.line(self._c("1", title))

    def kv(self, key: str, value: Any) -> None:
        self.line(f"  {self._c('2', f'{key:<14}')} {value}")

    def _trace(self, author: str, text: str) -> None:
        elapsed = self._c("2", f"[{time.monotonic() - self.started:6.1f}s]")
        self.line(f"  {elapsed} {self._c('35', f'{author:<28}')} {text}")

    def _author(self, author: str | None) -> str:
        author = author or "?"
        if author not in self.authors:
            self.authors.append(author)
        return author

    # ---- ADK events -----------------------------------------------------------------------

    def adk_event(self, event: Any) -> None:
        """Handles one ADK event, as a JSON dict (remote) or an Event model dump (in-process)."""
        self.events += 1
        if self.verbose:
            self.line(self._c("2", f"  [raw] {json.dumps(event, default=str)}"))
        if not isinstance(event, dict):
            self._trace("?", self._c("2", _short(str(event))))
            return
        if event.get("error") or event.get("error_code"):
            self.errors.append(event)
            self._trace(event.get("author") or "?", self._c("31", "✗ error ") + _short(event, 300))
            return

        usage = event.get("usage_metadata") or {}
        if usage and not event.get("partial"):
            self.tokens["prompt"] += usage.get("prompt_token_count") or 0
            self.tokens["output"] += usage.get("candidates_token_count") or 0
            self.tokens["total"] += usage.get("total_token_count") or 0

        content = event.get("content")
        if not (isinstance(content, dict) and content.get("parts")):
            return
        author = self._author(event.get("author"))
        for part in content["parts"]:
            if not isinstance(part, dict):
                continue
            if part.get("function_call"):
                call = part["function_call"]
                self.tool_calls += 1
                args = call.get("args") or {}
                if call.get("name") == "transfer_to_agent":
                    self._trace(author, self._c("33", "⇢ transfer_to_agent → ") + str(args.get("agent_name")))
                else:
                    self._trace(author, self._c("33", f"→ {call.get('name')}") + self._c("2", f"({_short(args, 100)})"))
            elif part.get("function_response"):
                response = part["function_response"]
                self._tool_result(author, response.get("name"), response.get("response"))
            elif part.get("text"):
                if part.get("thought"):
                    self._trace(author, self._c("2", "💭 " + _short(part["text"], 100)))
                elif not event.get("partial"):
                    self.answer_author, self.answer = author, part["text"]
                    self._trace(author, self._c("32", "💬 text ") + self._c("2", _short(part["text"], 70)))

    # ---- A2A tasks ------------------------------------------------------------------------

    def a2a_task(self, task: Any) -> None:
        """Handles the final A2A task (dict or SDK model)."""
        self.events += 1
        if hasattr(task, "model_dump"):
            task = task.model_dump(mode="json", exclude_none=True, by_alias=False)
        if self.verbose:
            self.line(self._c("2", f"  [raw] {json.dumps(task, default=str)}"))

        status = _get(task, "status") or {}
        state = _get(status, "state")
        self.task_state = str(getattr(state, "value", state) or "unknown")

        author = self._author(_get(task, "metadata", {}).get("adk_author") or "agent")
        for message in _get(task, "history") or []:
            for part in _get(message, "parts") or []:
                part = _get(part, "root", part)
                data, meta = _get(part, "data"), _get(part, "metadata") or {}
                kind = meta.get("adk_type") if isinstance(meta, dict) else None
                if not isinstance(data, dict):
                    continue
                if kind == "function_call":
                    self.tool_calls += 1
                    name, args = data.get("name"), data.get("args") or {}
                    self._trace(author, self._c("33", f"→ {name}") + self._c("2", f"({_short(args, 100)})"))
                elif kind == "function_response":
                    self._tool_result(author, data.get("name"), data.get("response"))

        usage = (_get(task, "metadata") or {}).get("adk_usage_metadata") or {}
        self.tokens["prompt"] += int(usage.get("promptTokenCount") or 0)
        self.tokens["output"] += int(usage.get("candidatesTokenCount") or 0)
        self.tokens["total"] += int(usage.get("totalTokenCount") or 0)

        texts = [
            text
            for artifact in _get(task, "artifacts") or []
            for part in _get(artifact, "parts") or []
            if (text := _get(_get(part, "root", part), "text"))
        ]
        if texts:
            self.answer_author, self.answer = author, "\n\n".join(texts)
            self._trace(author, self._c("32", "💬 text ") + self._c("2", _short(self.answer, 70)))
        if self.task_state.lower() not in _COMPLETED_STATES:
            message = " ".join(
                t for p in _get(_get(status, "message") or {}, "parts") or [] if (t := _get(_get(p, "root", p), "text"))
            )
            self.errors.append({"task_state": self.task_state, "message": message})
            self._trace(author, self._c("31", f"✗ task {self.task_state} ") + _short(message, 200))

    def _tool_result(self, author: str, name: Any, response: Any) -> None:
        failed = _is_tool_error(response)
        self.tool_errors += failed
        mark = self._c("31", "✗ error") if failed else self._c("32", "✓")
        self._trace(author, self._c("2", f"← {name} ") + mark + " " + self._c("2", _short(response, 90)))

    # ---- summary --------------------------------------------------------------------------

    def failures(self, *, fail_on_tool_error: bool = False) -> list[str]:
        problems = []
        if self.errors:
            problems.append(f"{len(self.errors)} error(s) reported by the agent")
        if not self.answer:
            problems.append("the agent returned no text answer")
        if fail_on_tool_error and self.tool_errors:
            problems.append(f"{self.tool_errors} tool call(s) returned an error")
        return problems

    def summary(self, *, fail_on_tool_error: bool = False) -> int:
        """Prints the answer and the result; returns the process exit code."""
        self.section("🤖 Answer" + (f" (from {self.answer_author})" if self.answer_author else ""))
        if self.answer:
            for row in self.answer.splitlines() or [""]:
                self.line(f"  {row}")
        else:
            self.line(self._c("33", "  (no text answer)"))

        self.section("📋 Result")
        if self.task_state:
            self.kv("task state", self.task_state)
        self.kv("events", self.events)
        self.kv("agents", " → ".join(self.authors) or "-")
        tools = str(self.tool_calls) + (self._c("31", f" ({self.tool_errors} errored)") if self.tool_errors else "")
        self.kv("tool calls", tools)
        self.kv(
            "tokens", f"prompt={self.tokens['prompt']}  output={self.tokens['output']}  total={self.tokens['total']}"
        )
        self.kv("time", f"{time.monotonic() - self.started:.1f}s")

        problems = self.failures(fail_on_tool_error=fail_on_tool_error)
        for error in self.errors:
            self.line(self._c("31", "  " + _short(error, 400)))
        if problems:
            self.line(self._c("31;1", "  ❌ FAILED: " + "; ".join(problems)))
            return 1
        if self.tool_errors:
            self.line(self._c("33", f"  ⚠️  {self.tool_errors} tool call(s) returned an error (see the trace)."))
        self.line(self._c("32;1", "  ✅ PASSED"))
        return 0
