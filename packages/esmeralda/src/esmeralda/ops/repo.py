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

"""The checkout: repository root, environments, Terragrunt units, state outputs and subprocesses.

The repository root is ``$ESMERALDA_REPO`` if set, else the first parent of the working directory
that contains ``infrastructure/live/root.hcl``.

Unit outputs come from the local state file (``infrastructure/live/.local_states/<env>/<unit>``)
when the environment uses the local backend, which is instant; otherwise from
``terragrunt output -json``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ENV_VAR = "ESMERALDA_REPO"
LIVE = Path("infrastructure/live")
UNIT_FILE = "terragrunt.hcl"
_SKIP_DIRS = {".terragrunt-cache", ".terraform", ".local_states"}


class OpsError(Exception):
    """An operation failed; the message says what to do about it."""


# ------------------------------------------------------------------------------------------
# repository, environments, units
# ------------------------------------------------------------------------------------------


def checkout(path: str | os.PathLike) -> Path:
    """``path`` as a repository root; raises if it is not an Esmeralda checkout."""
    root = Path(path).expanduser().resolve()
    if not (root / LIVE / "root.hcl").is_file():
        raise OpsError(f"{path} is not an Esmeralda checkout (no {LIVE}/root.hcl)")
    return root


def repo_root(start: str | os.PathLike | None = None) -> Path:
    """``$ESMERALDA_REPO``, else the nearest parent of ``start`` (default: cwd) with the live tree."""
    explicit = os.environ.get(REPO_ENV_VAR)
    if explicit:
        return checkout(explicit)
    here = Path(start or os.getcwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / LIVE / "root.hcl").is_file():
            return candidate
    raise OpsError(f"No Esmeralda checkout at or above {here}: run from the repo or set {REPO_ENV_VAR}=<repo root>")


def environments(root: Path) -> list[str]:
    """Environments: directories of ``infrastructure/live`` with an ``env.yaml``."""
    live = root / LIVE
    return sorted(p.name for p in live.iterdir() if (p / "env.yaml").is_file())


def env_dir(root: Path, env: str) -> Path:
    envs = environments(root)
    if env not in envs:
        raise OpsError(f"Unknown env '{env}'. Known: {', '.join(envs) or '(none)'}")
    return root / LIVE / env


def units(root: Path, env: str) -> list[str]:
    """Unit paths relative to the env directory (``layer-5-workloads/agents/x``), in apply-ish order."""
    base = env_dir(root, env)
    found = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        if UNIT_FILE in filenames:
            found.append(Path(dirpath).relative_to(base).as_posix())
    return sorted(found)


def unit_dir(root: Path, env: str, unit: str) -> Path:
    unit = unit.strip().strip("/")
    known = units(root, env)
    if unit not in known:
        close = [u for u in known if unit and unit.split("/")[-1] in u]
        hint = f" Did you mean: {', '.join(close)}?" if close else f" Known: {', '.join(known)}"
        raise OpsError(f"Unknown unit '{unit}' in {env}.{hint}")
    return env_dir(root, env) / unit


def env_locals(root: Path, env: str) -> str:
    return (env_dir(root, env) / "env.yaml").read_text()


def _hcl_string(text: str, key: str) -> str:
    match = re.search(rf'^\s*{key}\s*=\s*"([^"]*)"', text, re.MULTILINE)
    return match.group(1) if match else ""


def backend(root: Path, env: str) -> str:
    """``gcs`` when env.yaml sets both state_project and state_bucket (as root.hcl decides), else ``local``."""
    text = env_locals(root, env)
    return "gcs" if _hcl_string(text, "state_project") and _hcl_string(text, "state_bucket") else "local"


def gateways_block(root: Path, env: str) -> dict[str, Any]:
    """The ``gateways = { enabled, default, host_rules }`` block of env.yaml, parsed."""
    text = env_locals(root, env)
    match = re.search(r"^\s*gateways\s*=\s*\{(.*?)^\s*\}", text, re.MULTILINE | re.DOTALL)
    if not match:
        return {}
    body = re.sub(r"#.*", "", match.group(1))
    enabled = re.search(r"enabled\s*=\s*\[([^\]]*)\]", body)
    default = re.search(r'default\s*=\s*"([^"]*)"', body)
    rules = re.search(r"host_rules\s*=\s*\{([^}]*)\}", body)
    return {
        "enabled": re.findall(r'"([^"]+)"', enabled.group(1)) if enabled else [],
        "default": default.group(1) if default else None,
        "host_rules": dict(re.findall(r'"?([\w.-]+)"?\s*=\s*"([^"]+)"', rules.group(1))) if rules else {},
    }


# ------------------------------------------------------------------------------------------
# state and outputs
# ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitState:
    unit: str
    deployed: bool | None  # None: unknown (remote backend and outputs unavailable)
    resources: int | None
    outputs: dict[str, Any]
    sensitive: list[str]


def state_path(root: Path, env: str, unit: str) -> Path:
    return root / LIVE / ".local_states" / env / unit / "terraform.tfstate"


def parse_state(unit: str, data: dict[str, Any]) -> UnitState:
    """Outputs (sensitive ones dropped) and managed resource count of a Terraform state document."""
    outputs, sensitive = {}, []
    for name, output in (data.get("outputs") or {}).items():
        if output.get("sensitive"):
            sensitive.append(name)
        else:
            outputs[name] = output.get("value")
    managed = [r for r in data.get("resources") or [] if r.get("mode") == "managed"]
    return UnitState(unit, bool(managed), len(managed), outputs, sensitive)


def parse_output_json(unit: str, data: dict[str, Any]) -> UnitState:
    """``terragrunt output -json`` (no resource count in it)."""
    outputs = {k: v.get("value") for k, v in data.items() if not v.get("sensitive")}
    sensitive = [k for k, v in data.items() if v.get("sensitive")]
    return UnitState(unit, bool(data) or None, None, outputs, sensitive)


def unit_state(root: Path, env: str, unit: str, *, timeout: float = 120.0) -> UnitState:
    if backend(root, env) == "local":
        path = state_path(root, env, unit)
        if not path.is_file():
            return UnitState(unit, False, 0, {}, [])
        try:
            return parse_state(unit, json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError) as exc:
            raise OpsError(f"Unreadable state {path}: {exc}") from exc
    result = run(
        terragrunt_cmd("output", "-json"), cwd=unit_dir(root, env, unit), timeout=timeout, what="terragrunt output"
    )
    if result.returncode != 0:
        return UnitState(unit, None, None, {}, [])
    try:
        return parse_output_json(unit, json.loads(result.stdout or "{}"))
    except json.JSONDecodeError:
        return UnitState(unit, None, None, {}, [])


def all_states(root: Path, env: str) -> list[UnitState]:
    names = units(root, env)
    if backend(root, env) == "local":
        return [unit_state(root, env, u) for u in names]
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(lambda u: unit_state(root, env, u), names))


def output(root: Path, env: str, unit: str, name: str) -> Any:
    return unit_state(root, env, unit).outputs.get(name)


# ------------------------------------------------------------------------------------------
# subprocesses and errors
# ------------------------------------------------------------------------------------------


def tool_env() -> dict[str, str]:
    """os.environ plus the per-user Terraform / Terragrunt bin directories (as the Makefile does)."""
    environ = dict(os.environ)
    home = Path.home()
    extra = [str(home / ".terraform" / "bin"), str(home / ".terragrunt" / "bin")]
    environ["PATH"] = os.pathsep.join([*extra, environ.get("PATH", "")])
    environ.setdefault("TG_NON_INTERACTIVE", "true")
    return environ


def which(binary: str) -> str | None:
    return shutil.which(binary, path=tool_env()["PATH"])


def terragrunt_cmd(*args: str) -> list[str]:
    return ["terragrunt", "--non-interactive", *args]


def run(cmd: list[str], *, cwd: Path, timeout: float, what: str) -> subprocess.CompletedProcess[str]:
    """Runs ``cmd`` with captured output; raises OpsError with a hint on a missing binary or timeout."""
    if not which(cmd[0]):
        raise OpsError(f"{cmd[0]} not found on PATH: {INSTALL_HINTS.get(cmd[0], f'install {cmd[0]}')}")
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=tool_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise OpsError(f"{what} timed out after {timeout:.0f}s in {cwd}") from exc


INSTALL_HINTS = {
    "terragrunt": "install Terragrunt (see the repo README, Prerequisites)",
    "terraform": "install Terraform (see the repo README, Prerequisites)",
    "gcloud": "install the Google Cloud CLI",
    "bash": "bash is required",
}

# (pattern, hint): the first match wins. Patterns are matched case-insensitively against stderr.
_HINTS = [
    (
        r"reauthentication|gcloud auth login|refresh token.*(expired|revoked)|invalid_grant|token has been expired",
        "gcloud auth expired: run `gcloud auth login` and `gcloud auth application-default login`",
    ),
    (
        r"could not find default credentials|default credentials were not found|application default credentials",
        "no Application Default Credentials: run `gcloud auth application-default login`",
    ),
    (
        r"ESMERALDA_BILLING_ACCOUNT",
        "ESMERALDA_BILLING_ACCOUNT is not set in the MCP server's environment: "
        "export it (or set it in the client's env)",
    ),
    (
        r"permission[_ ]denied|\b403\b|does not have .*permission",
        "permission denied: check the active account (`gcloud auth list`) and its IAM roles on the project",
    ),
    (r"error acquiring the state lock", "state is locked by another run: wait for it, or check for a stuck lock"),
    (
        r"no such host|connection refused|i/o timeout|network is unreachable",
        "network error reaching Google APIs: check connectivity / proxy settings",
    ),
    (
        r"does not match any of the checksums",
        "provider plugin cache conflict (TF_PLUGIN_CACHE_DIR is not safe for Terragrunt's parallel dependency inits): "
        "unset TF_PLUGIN_CACHE_DIR and set TG_PROVIDER_CACHE=1 in the MCP server's environment",
    ),
    (
        r"terraform init|module not installed|provider.*not.*installed|inconsistent dependency lock",
        "Terraform needs init: run `terragrunt init` in the unit (or let plan re-run init)",
    ),
]


def hint(text: str) -> str | None:
    for pattern, message in _HINTS:
        if re.search(pattern, text, re.IGNORECASE):
            return message
    return None


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Terragrunt log prefix: "03:12:25.595 ERROR  " / "INFO   [unit] "
_TG_PREFIX = re.compile(r"^\s*\d\d:\d\d:\d\d(\.\d+)?\s+(ERROR|WARN|INFO|DEBUG|STDERR|STDOUT)\s+(\[[^\]]*\]\s+)?")


def error_lines(text: str, limit: int = 6) -> list[str]:
    """The informative lines of a failing command's output: ``Error:`` lines first, else the tail."""
    lines = []
    for raw in text.splitlines():
        line = _TG_PREFIX.sub("", _ANSI.sub("", raw)).strip().strip("│╷╵").strip()
        if line and line not in lines:
            lines.append(line)
    errors = [line for line in lines if re.search(r"\bError:|\bERROR:|failed", line)]
    picked = errors or [line for line in lines if re.search(r"error", line, re.I)] or lines
    return [line[:300] for line in picked[-limit:]]


def failure(what: str, result: subprocess.CompletedProcess[str]) -> OpsError:
    """An OpsError for a failed command, with the matched hint and the key error lines."""
    text = f"{result.stderr}\n{result.stdout}"
    found = hint(text)
    details = " | ".join(error_lines(text))
    prefix = f"{what} failed (exit {result.returncode})"
    return OpsError(f"{prefix}: {found}. Details: {details}" if found else f"{prefix}: {details}")


def short(value: Any, limit: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
