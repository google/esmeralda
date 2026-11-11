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

"""Static guard: the delegated user credential never goes into OTel baggage or span attributes.

Scans every tracked Python file (outside tests) for a call that writes baggage or span data
(``set_baggage``, ``set_attribute(s)``, ``add_event``, ``start_as_current_span`` / ``start_span``
``attributes=``, ``record_exception`` ``attributes=``) and that mentions the user credential:
``user_auth_token``, ``USER_TOKEN_KEY``, ``user_token``, ``user_token_state_key``, ``resolve_user_token``
or ``outgoing_headers``, as a name, attribute, keyword or string literal.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

SINK_CALLS = frozenset({"set_baggage", "set_attribute", "set_attributes", "add_event"})
SINK_ATTR_KWARG_CALLS = frozenset({"start_as_current_span", "start_span", "record_exception"})
CREDENTIAL_MARKERS = (
    "user_auth_token",
    "user_token_key",
    "user_token",
    "user_token_state_key",
    "resolve_user_token",
    "outgoing_headers",
    "x-user-token",
    "user-auth-token",
)


def _mentions_credential(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        text = None
        if isinstance(sub, ast.Name):
            text = sub.id
        elif isinstance(sub, ast.Attribute):
            text = sub.attr
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            text = sub.value
        elif isinstance(sub, ast.keyword) and sub.arg:
            text = sub.arg
        if text and any(marker in text.lower() for marker in CREDENTIAL_MARKERS):
            return True
    return False


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def find_violations(source: str, filename: str = "<string>") -> list[str]:
    """``file:line: call`` for each baggage / span write that carries the user credential."""
    violations = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in SINK_CALLS:
            payload = [*node.args, *node.keywords]
        elif name in SINK_ATTR_KWARG_CALLS:
            payload = [k for k in node.keywords if k.arg == "attributes"]
        else:
            continue
        if any(_mentions_credential(p) for p in payload):
            violations.append(f"{filename}:{node.lineno}: {name}")
    return violations


def _tracked_python_files() -> list[pathlib.Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.split()
    files = []
    for rel in out:
        path = REPO_ROOT / rel
        parts = pathlib.PurePosixPath(rel).parts
        if "tests" in parts or path.name.startswith("test_") or not path.is_file():
            continue
        files.append(path)
    return files


def test_no_user_credential_in_baggage_or_span_attributes():
    violations = []
    for path in _tracked_python_files():
        violations += find_violations(path.read_text(encoding="utf-8"), str(path.relative_to(REPO_ROOT)))
    assert not violations, "user credential written to baggage or span attributes:\n" + "\n".join(violations)


def test_guard_catches_planted_violations():
    planted = """
from opentelemetry import baggage, trace
from esmeralda import context
ctx = baggage.set_baggage("user", context.user_token(ic))
ctx = baggage.set_baggage(context.USER_TOKEN_KEY, value)
span.set_attribute("enduser.token", state["temp:user_auth_token"])
span.set_attributes({"x": user_token})
span.add_event("auth", {"h": headers["X-User-Token"]})
with tracer.start_as_current_span("call", attributes={"t": resolve_user_token(ctx)}):
    pass
"""
    assert len(find_violations(planted)) == 6

    clean = """
ctx = baggage.set_baggage("caller.project_id", caller.project_id)
span.set_attribute("caller.agent_name", caller.agent_name)
headers.update(user_credential.outgoing_headers(url, ctx))
with tracer.start_as_current_span("call", attributes={"tool": name}):
    pass
"""
    assert find_violations(clean) == []
