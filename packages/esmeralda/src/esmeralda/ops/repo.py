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

from esmeralda.ops import envfile

REPO_ENV_VAR = "ESMERALDA_REPO"
LIVE = Path("infrastructure/live")
UNIT_FILE = "terragrunt.hcl"
_SKIP_DIRS = {".terragrunt-cache", ".terraform", ".local_states"}

# One-release fallback for units flattened from layer-5-workloads/services/* to layer-5-workloads/*.
_MOVED_UNITS: dict[str, str] = {
    "layer-5-workloads/services/test-vm": "layer-5-workloads/test-vm",
    "layer-5-workloads/services/iap-egress": "layer-5-workloads/iap-egress",
}
_LEGACY_UNIT_PATHS: dict[str, str] = {v: k for k, v in _MOVED_UNITS.items()}


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
    if unit not in known and unit in _MOVED_UNITS and _MOVED_UNITS[unit] in known:
        unit = _MOVED_UNITS[unit]
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
    primary = root / LIVE / ".local_states" / env / unit / "terraform.tfstate"
    if not primary.is_file() and unit in _LEGACY_UNIT_PATHS:
        legacy = root / LIVE / ".local_states" / env / _LEGACY_UNIT_PATHS[unit] / "terraform.tfstate"
        if legacy.is_file():
            return legacy
    return primary


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
    """os.environ plus the per-user Terraform / Terragrunt bin directories (as the Makefile does).

    ``ESMERALDA_*`` variables missing from the environment are filled from the user-local env file
    (``$ESMERALDA_ENV_FILE`` or ``~/.config/esmeralda/env``, outside the repo); the environment wins.
    """
    environ = dict(os.environ)
    envfile.apply(environ)
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
    "git": "install Git",
    "uv": "install uv (curl -LsSf https://astral.sh/uv/install.sh | sh)",
    "terragrunt": "install Terragrunt (see the repo README, Prerequisites)",
    "terraform": "install Terraform (see the repo README, Prerequisites)",
    "gcloud": "install the Google Cloud CLI",
    "bash": "bash is required",
}

_DIGEST_RE = re.compile(r"^(?P<repo>[^@\s]+)@sha256:(?P<sha>[0-9a-f]{64})$")
_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def extract_image_digests(data: dict[str, Any]) -> list[str]:
    """Every ``image`` / ``image_uri`` attribute pinned by ``@sha256:...`` in a Terraform state dict."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("image", "image_uri") and isinstance(value, str) and _DIGEST_RE.match(value):
                    if value not in found:
                        found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for resource in data.get("resources") or []:
        if resource.get("mode") == "managed":
            walk([inst.get("attributes") for inst in resource.get("instances") or []])
    return found


def git_info(root: Path) -> dict[str, Any]:
    """Git branch, HEAD SHA, dirty/clean status and active worktrees for ``root``."""
    branch = "unknown"
    head_sha = "unknown"
    dirty = False
    dirty_count = 0
    worktrees: list[dict[str, str]] = []

    try:
        res_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if res_branch.returncode == 0 and res_branch.stdout.strip():
            branch = res_branch.stdout.strip()

        res_sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if res_sha.returncode == 0 and res_sha.stdout.strip():
            head_sha = res_sha.stdout.strip()

        res_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if res_status.returncode == 0:
            lines = [ln for ln in res_status.stdout.splitlines() if ln.strip()]
            dirty_count = len(lines)
            dirty = dirty_count > 0

        res_wt = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if res_wt.returncode == 0:
            current: dict[str, str] = {}
            for line in res_wt.stdout.splitlines():
                if not line.strip():
                    if current:
                        worktrees.append(current)
                        current = {}
                    continue
                if line.startswith("worktree "):
                    current["path"] = line.split(" ", 1)[1].strip()
                elif line.startswith("HEAD "):
                    current["head"] = line.split(" ", 1)[1].strip()[:8]
                elif line.startswith("branch "):
                    ref = line.split(" ", 1)[1].strip()
                    current["branch"] = ref.removeprefix("refs/heads/")
                elif line.strip() == "detached":
                    current["branch"] = "(detached)"
            if current:
                worktrees.append(current)
    except (OSError, subprocess.SubprocessError):
        pass

    return {
        "branch": branch,
        "head_sha": head_sha,
        "dirty": dirty,
        "dirty_files": dirty_count,
        "worktrees": worktrees,
    }


def status(root: Path, env: str = "dev") -> dict[str, Any]:
    """Combined repository and environment status: git, worktrees, local states, image tags/digests, outputs."""
    from esmeralda.ops import infra

    env_dir(root, env)
    ginfo = git_info(root)
    text = env_locals(root, env)
    image_repo = _hcl_string(text, "image_repository") or "dev"
    container_tag = _hcl_string(text, "container_tag") or "dev-latest"
    head_sha = ginfo["head_sha"]
    expected_sha_tag = f"dev-{head_sha}" if head_sha != "unknown" else "unknown"

    local_state_units: list[str] = []
    image_digests: dict[str, list[str]] = {}
    for u in units(root, env):
        spath = state_path(root, env, u)
        if spath.is_file():
            local_state_units.append(u)
            try:
                raw_state = json.loads(spath.read_text())
                digests = extract_image_digests(raw_state)
                if digests:
                    image_digests[u] = digests
            except (OSError, json.JSONDecodeError):
                pass

    infra_summary = infra.status(root, env)
    return {
        **infra_summary,
        "git": ginfo,
        "local_state_units": local_state_units,
        "images": {
            "image_repository": image_repo,
            "container_tag": container_tag,
            "expected_sha_tag": expected_sha_tag,
            "matches_head_sha_tag": container_tag == expected_sha_tag,
            "digests": image_digests,
        },
    }


def _tool_version(binary: str, args: list[str], cwd: Path) -> str:
    try:
        res = subprocess.run(
            [binary, *args],
            cwd=cwd,
            env=tool_env(),
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        out = f"{res.stdout}\n{res.stderr}".strip()
        match = _VERSION_RE.search(out)
        return match.group(1) if match else (out.splitlines()[0][:60] if out else "installed")
    except (OSError, subprocess.SubprocessError):
        return "installed"


def _check_adc(root: Path) -> tuple[bool, str]:
    adc_env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    adc_path = (
        Path(adc_env).expanduser()
        if adc_env
        else Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    )
    if which("gcloud"):
        try:
            res = subprocess.run(
                ["gcloud", "auth", "application-default", "print-access-token"],
                cwd=root,
                env=tool_env(),
                capture_output=True,
                text=True,
                timeout=15,
                stdin=subprocess.DEVNULL,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                return True, "ADC token available"
        except (OSError, subprocess.SubprocessError):
            pass
    if adc_path.is_file():
        return (
            False,
            f"ADC file exists ({adc_path.name}) but token refresh failed: run `gcloud auth application-default login`",
        )
    return False, "missing Application Default Credentials: run `gcloud auth application-default login`"


def _check_uv_lock(root: Path) -> tuple[bool, str]:
    lock_file = root / "uv.lock"
    if not lock_file.is_file():
        return False, "uv.lock not found in repository root"
    if not (root / "pyproject.toml").is_file() or not which("uv"):
        return True, "uv.lock present"
    try:
        res = subprocess.run(
            ["uv", "lock", "--check"],
            cwd=root,
            env=tool_env(),
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        if res.returncode == 0:
            return True, "uv.lock is in sync with pyproject.toml"
        return False, "uv.lock is out of sync with pyproject.toml (run `make lock`)"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"uv lock --check failed: {exc}"


def _check_local_state_dir(root: Path, env: str) -> tuple[bool, str]:
    live_dir = root / LIVE
    env_path = live_dir / env
    if not (env_path / "env.yaml").is_file():
        return False, f"missing environment config {LIVE}/{env}/env.yaml"
    state_root = live_dir / ".local_states" / env
    if not state_root.exists():
        if os.access(live_dir, os.R_OK | os.W_OK):
            return True, f"{LIVE}/.local_states/{env} not created yet (parent directory is readable/writable)"
        return False, f"{LIVE} is not readable/writable"
    if not os.access(state_root, os.R_OK):
        return False, f"{state_root.relative_to(root)} is not readable"
    unreadable: list[str] = []
    count = 0
    for tfstate in state_root.rglob("terraform.tfstate"):
        count += 1
        try:
            json.loads(tfstate.read_text())
        except (OSError, json.JSONDecodeError):
            unreadable.append(str(tfstate.relative_to(root)))
    if unreadable:
        return False, f"unreadable state file(s): {', '.join(unreadable)}"
    return True, f"readable ({count} state file(s) in {LIVE}/.local_states/{env})"


def _env_checks() -> list[dict[str, Any]]:
    """The user-local env file and where each Terragrunt variable comes from (values never shown)."""
    found = envfile.read()
    checks: list[dict[str, Any]] = []
    if found.error:
        checks.append({"name": "env_file", "status": "warn", "required": False, "detail": found.error})
    elif not found.exists:
        checks.append(
            {
                "name": "env_file",
                "status": "ok",
                "required": False,
                "detail": f"{found.path} not present (optional; ${envfile.ENV_FILE_VAR} overrides the path)",
            }
        )
    elif found.too_open:
        checks.append(
            {
                "name": "env_file",
                "status": "warn",
                "required": False,
                "detail": f"{found.path} is accessible by group/others: run `chmod 600 {found.path}`",
            }
        )
    elif found.quoted or found.ignored:
        problems = []
        if found.quoted:
            problems.append(f"quoted values for {', '.join(found.quoted)} (make keeps the quotes: remove them)")
        if found.ignored:
            problems.append(f"non-ESMERALDA_* keys {', '.join(found.ignored)} (make would read them: remove them)")
        checks.append(
            {
                "name": "env_file",
                "status": "warn",
                "required": False,
                "detail": f"{found.path}: use plain KEY=VALUE lines; " + "; ".join(problems),
            }
        )
    else:
        keys = ", ".join(found.keys()) or "no ESMERALDA_* keys"
        checks.append({"name": "env_file", "status": "ok", "required": False, "detail": f"{found.path} ({keys})"})

    where = {"env": "set (from the environment)", "env-file": f"set (from {found.path})"}
    origin = envfile.source("ESMERALDA_BILLING_ACCOUNT", env_file=found)
    checks.append(
        {
            "name": "env:ESMERALDA_BILLING_ACCOUNT",
            "status": "ok" if origin != "missing" else "warn",
            "required": False,
            "source": origin,
            "detail": where.get(
                origin,
                f"missing: add ESMERALDA_BILLING_ACCOUNT=... to {found.path} (outside the repo, never commit it) "
                "or export it before terragrunt plan/apply",
            ),
        }
    )
    origin = envfile.source("ESMERALDA_ORG_ID", env_file=found)
    checks.append(
        {
            "name": "env:ESMERALDA_ORG_ID",
            "status": "ok",
            "required": False,
            "source": origin,
            "detail": where.get(origin, "unset (optional)"),
        }
    )
    return checks


def doctor(root: Path, env: str = "dev") -> dict[str, Any]:
    """Workstation preflight checks for ``root`` and ``env``."""
    checks: list[dict[str, Any]] = []

    required_tools = [
        ("git", ["--version"]),
        ("uv", ["--version"]),
        ("terraform", ["version"]),
        ("terragrunt", ["--version"]),
        ("gcloud", ["--version"]),
    ]
    for name, ver_args in required_tools:
        resolved = which(name)
        if resolved:
            ver = _tool_version(resolved, ver_args, root)
            checks.append({"name": f"tool:{name}", "status": "ok", "required": True, "detail": f"{ver} ({resolved})"})
        else:
            checks.append(
                {
                    "name": f"tool:{name}",
                    "status": "fail",
                    "required": True,
                    "detail": f"not found on PATH ({INSTALL_HINTS.get(name, f'install {name}')})",
                }
            )

    checks.extend(_env_checks())

    adc_ok, adc_detail = _check_adc(root)
    checks.append(
        {
            "name": "adc_token",
            "status": "ok" if adc_ok else "fail",
            "required": True,
            "detail": adc_detail,
        }
    )

    lock_ok, lock_detail = _check_uv_lock(root)
    checks.append(
        {
            "name": "uv_lock_frozen",
            "status": "ok" if lock_ok else "fail",
            "required": True,
            "detail": lock_detail,
        }
    )

    state_ok, state_detail = _check_local_state_dir(root, env)
    checks.append(
        {
            "name": "local_state_dir",
            "status": "ok" if state_ok else "fail",
            "required": True,
            "detail": state_detail,
        }
    )

    ok = all(c["status"] == "ok" for c in checks if c["required"])
    return {
        "env": env,
        "ok": ok,
        "failed": sum(1 for c in checks if c["status"] == "fail" and c["required"]),
        "warnings": sum(1 for c in checks if c["status"] == "warn"),
        "checks": checks,
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
        "ESMERALDA_BILLING_ACCOUNT is not set: add it to the user-local env file "
        "~/.config/esmeralda/env (or the file named by $ESMERALDA_ENV_FILE; outside the repo, chmod 600, "
        "never commit it), export it, or set it in the MCP client's env",
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
