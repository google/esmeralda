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
"""Key outputs of every deployed unit of an environment, in one fast read.

Reads Terraform state outputs directly instead of calling `terragrunt output` per unit (which
resolves every dependency and takes seconds each):
  * local backend (env.yaml without state_project/state_bucket):
      infrastructure/live/.local_states/<env>/<unit>/terraform.tfstate
  * gcs backend: gs://<state_bucket>/<env>/<unit>/terraform.tfstate/default.tfstate (gcloud storage
    cat, in parallel), falling back to `terragrunt output -json` per unit.

Prints a compact table of the key outputs (entry point, gateways, agent engines, MCP services,
test VM) plus the image digests found in local state, or everything as JSON with --json.

Usage: scripts/env_status.py <env> [--json] [--all]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
LIVE_DIR = REPO_ROOT / "infrastructure" / "live"

# Output names worth a line in the table (everything is in --json / --all).
KEY_OUTPUT = re.compile(
    r"(^ip_address$|^entrypoint_(address|domain)$|^routing$|^routes$|^backend_service_id$|^agent_gateway_id$"
    r"|^engine_id$|^service_uri$|^instance_name$|^instance_zone$)"
)
_DIGEST = re.compile(r"^(?P<repo>[^@\s]+)@sha256:(?P<sha>[0-9a-f]{64})$")


def env_locals(env: str) -> dict[str, str]:
    """String locals of env.yaml (an HCL locals block despite the name); enough for the backend."""
    text = (LIVE_DIR / env / "env.yaml").read_text()
    return dict(re.findall(r'^\s*([a-z_]+)\s*=\s*"([^"]*)"', text, flags=re.M))


def discover_units(env: str) -> list[str]:
    root = LIVE_DIR / env
    units = [
        str(p.parent.relative_to(root))
        for p in root.rglob("terragrunt.hcl")
        if ".terragrunt-cache" not in p.parts and p.parent != root
    ]
    return sorted(units)


def gcs_state_uri(bucket: str, env: str, unit: str) -> str:
    """root.hcl: prefix = "<path_relative_to_include>/terraform.tfstate"; the gcs backend adds default.tfstate."""
    return f"gs://{bucket}/{env}/{unit}/terraform.tfstate/default.tfstate"


def read_state(env: str, unit: str, backend: dict[str, str]) -> dict[str, Any] | None:
    """Returns the parsed state, {"outputs": ...} from terragrunt, or None when not deployed."""
    if backend["type"] == "local":
        path = LIVE_DIR / ".local_states" / env / unit / "terraform.tfstate"
        if not path.is_file():
            return None
        state = json.loads(path.read_text() or "{}")
        return state if state.get("resources") or state.get("outputs") else None
    uri = gcs_state_uri(backend["bucket"], env, unit)
    res = subprocess.run(["gcloud", "storage", "cat", uri], capture_output=True, text=True, check=False)
    if res.returncode == 0 and res.stdout.strip():
        state = json.loads(res.stdout)
        return state if state.get("resources") or state.get("outputs") else None
    if "matched no objects" in res.stderr or "No URLs matched" in res.stderr or "404" in res.stderr:
        return None
    env_vars = {**os.environ, "TG_NON_INTERACTIVE": "true", "TG_LOG_LEVEL": "error", "TF_IN_AUTOMATION": "1"}
    res = subprocess.run(
        ["terragrunt", "output", "-json"],
        cwd=LIVE_DIR / env / unit,
        capture_output=True,
        text=True,
        env=env_vars,
        check=False,
    )
    if res.returncode != 0 or not res.stdout.strip().startswith("{"):
        return None
    outputs = json.loads(res.stdout)
    return {"outputs": outputs} if outputs else None


def image_digests(state: dict[str, Any]) -> list[str]:
    """Every `image` attribute pinned by digest (Cloud Run services, VMs) found in the state."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("image", "image_uri") and isinstance(value, str) and _DIGEST.match(value):
                    if value not in found:
                        found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for resource in state.get("resources") or []:
        if resource.get("mode") == "managed":
            walk([inst.get("attributes") for inst in resource.get("instances") or []])
    return found


def short_digest(image: str) -> str:
    m = _DIGEST.match(image)
    return f"{m['repo'].rsplit('/', 1)[-1]}@sha256:{m['sha'][:12]}" if m else image


def unit_outputs(state: dict[str, Any]) -> dict[str, Any]:
    return {
        name: ("<sensitive>" if out.get("sensitive") else out.get("value"))
        for name, out in (state.get("outputs") or {}).items()
    }


def collect(env: str, units: list[str] | None = None) -> dict[str, Any]:
    loc = env_locals(env)
    gcs = bool(loc.get("state_project") and loc.get("state_bucket"))
    backend = {"type": "gcs", "bucket": loc.get("state_bucket", "")} if gcs else {"type": "local"}
    units = units if units is not None else discover_units(env)
    with ThreadPoolExecutor(max_workers=8) as pool:
        states = dict(zip(units, pool.map(lambda u: read_state(env, u, backend), units)))
    result: dict[str, Any] = {"env": env, "backend": backend["type"], "units": {}, "not_deployed": []}
    for unit in units:
        state = states[unit]
        if state is None:
            result["not_deployed"].append(unit)
            continue
        entry: dict[str, Any] = {"outputs": unit_outputs(state)}
        digests = image_digests(state)
        if digests:
            entry["images"] = digests
        result["units"][unit] = entry
    return result


def compact(name: str, value: Any, limit: int = 110) -> str:
    """One-line rendering; gateway routes become `host(probe)` lists."""
    if name == "routes" and isinstance(value, dict):
        text = ", ".join(
            f"{v.get('host', k).split('.')[0]}({v.get('probe', '?')})" if isinstance(v, dict) else k
            for k, v in sorted(value.items())
        )
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, separators=(",", ":"))
    else:
        text = str(value)
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_table(data: dict[str, Any], show_all: bool = False) -> str:
    rows: list[tuple[str, str, str]] = []
    for unit, entry in data["units"].items():
        outs = entry["outputs"]
        keys = [k for k in sorted(outs) if show_all or KEY_OUTPUT.search(k)]
        keys = [k for k in keys if outs[k] not in ("", None, [], {})]
        for k in keys:
            rows.append((unit, k, compact(k, outs[k])))
        for img in entry.get("images", []):
            rows.append((unit, "image", short_digest(img)))
        if not keys and not entry.get("images"):
            rows.append((unit, "-", "deployed"))
    w_unit = max([len(r[0]) for r in rows] + [4])
    w_key = max([len(r[1]) for r in rows] + [6])
    lines = [f"📦 {data['env']} ({data['backend']} state): {len(data['units'])} deployed unit(s)"]
    lines.append(f"{'UNIT':<{w_unit}}  {'OUTPUT':<{w_key}}  VALUE")
    last = None
    for unit, key, value in rows:
        lines.append(f"{(unit if unit != last else ''):<{w_unit}}  {key:<{w_key}}  {value}")
        last = unit
    if data["not_deployed"]:
        lines.append("not deployed: " + ", ".join(data["not_deployed"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("env")
    parser.add_argument("--json", action="store_true", help="print every output as JSON")
    parser.add_argument("--all", action="store_true", help="show every output in the table, not just the key ones")
    args = parser.parse_args(argv)
    if not (LIVE_DIR / args.env / "env.yaml").is_file():
        print(f"❌ No environment '{args.env}' (missing infrastructure/live/{args.env}/env.yaml)", file=sys.stderr)
        return 1
    data = collect(args.env)
    print(json.dumps(data, indent=2) if args.json else render_table(data, show_all=args.all))
    return 0 if data["units"] else 1


if __name__ == "__main__":
    sys.exit(main())
