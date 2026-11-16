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

"""Offline helpers for Apigee bundle tests: run a JavaScript policy resource and render a message template."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_HARNESS = """
const fs = require('fs');
const store = Object.assign({}, JSON.parse(process.env.INITIAL_VARS || '{}'));
global.properties = JSON.parse(process.env.PROPERTIES || '{}');
global.context = {
  getVariable: function(name) {
    return Object.prototype.hasOwnProperty.call(store, name) ? store[name] : null;
  },
  setVariable: function(name, value) {
    store[name] = value;
  }
};
eval(fs.readFileSync(process.env.SCRIPT_PATH, 'utf8'));
process.stdout.write(JSON.stringify(store));
"""


def find_node_binary() -> str | None:
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


def run_apigee_js(
    script_path: Path, initial_vars: Mapping[str, Any], properties: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Executes an Apigee JavaScript resource against a mocked `context` (and policy `properties`)."""
    node_bin = find_node_binary()
    assert node_bin is not None, "Node.js binary is required to run Apigee JS policy tests"
    proc = subprocess.run(
        [node_bin, "-e", _HARNESS],
        env={
            "INITIAL_VARS": json.dumps(dict(initial_vars)),
            "PROPERTIES": json.dumps(dict(properties or {})),
            "SCRIPT_PATH": str(script_path),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def _split_args(raw: str) -> list[str]:
    """Splits message-template function arguments on top-level commas."""
    args, depth, quote, current = [], 0, "", ""
    for char in raw:
        if quote:
            current += char
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            args.append(current.strip())
            current = ""
            continue
        current += char
    args.append(current.strip())
    return args


def _evaluate(expr: str, variables: Mapping[str, Any]) -> str | None:
    """Evaluates the subset of Apigee message-template syntax the bundles use."""
    expr = expr.strip()
    call = re.fullmatch(r"([A-Za-z]+)\((.*)\)", expr, flags=re.DOTALL)
    if call:
        name, args = call.group(1), _split_args(call.group(2))
        if name == "firstnonnull":
            for arg in args:
                value = _evaluate(arg, variables)
                if value is not None:
                    return value
            return None
        if name == "escapeJSON":
            value = _evaluate(args[0], variables)
            return None if value is None else json.dumps(value)[1:-1]
        raise AssertionError(f"Unsupported message template function {name!r}")
    if len(expr) >= 2 and expr[0] == expr[-1] and expr[0] in "\"'":
        return expr[1:-1]
    if re.fullmatch(r"-?\d+(\.\d+)?", expr):
        return expr
    value = variables.get(expr)
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render_message_template(template: str, variables: Mapping[str, Any]) -> str:
    """Renders an Apigee message template: `{expr}` placeholders; unresolved variables render empty.

    A `{` starts a placeholder only when followed by a variable or function name, so literal JSON
    braces in the template stay as they are (as in Apigee).
    """
    out, i = [], 0
    while i < len(template):
        match = re.match(r"\{([A-Za-z_][A-Za-z0-9_.\-]*(\((?:[^{}]*)\))?)\}", template[i:])
        if match:
            value = _evaluate(match.group(1), variables)
            out.append("" if value is None else value)
            i += match.end()
        else:
            out.append(template[i])
            i += 1
    return "".join(out)
