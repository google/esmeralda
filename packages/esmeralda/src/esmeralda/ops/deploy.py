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

"""Whole-layer and whole-environment deploys, and the dev-only destroy.

``deploy`` applies units one at a time through ``infra.apply_unit`` (plan to a planfile, destroy
guard, apply the saved planfile), in the order the Makefile's ``deploy-*`` chains used:

* layer 0: the shared CI/CD unit (``shared/layer-0-cicd``; refused from a git worktree whose
  layer-0 state lives in the main checkout, unless ``--cicd-dir`` is explicit);
* layers 1-4: ``layer-1-projects`` -> ``layer-2-networking`` -> ``layer-3-security`` -> ``layer-4-governance``;
* layer 5: every enabled ``layer-5-workloads`` unit in Terragrunt's ``run --all`` order (dependency
  depth, then path), with ``iap-egress`` always last; then the running-image check
  (``release.verify_images``);
* ``--all``: layer 0 -> 1-3 -> 4 -> image builds -> layer 5 (as ``make deploy-all``).

It stops at the first failure or destroy-guard refusal and reports every step. ``dry_run`` plans
each unit (read-only) and prints the build commands instead.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from esmeralda.ops import build, infra, release, repo
from esmeralda.ops.repo import OpsError

FOUNDATIONS = {1: "layer-1-projects", 2: "layer-2-networking", 3: "layer-3-security", 4: "layer-4-governance"}
WORKLOADS = "layer-5-workloads"
FINAL_UNIT = "layer-5-workloads/iap-egress"
DEFAULT_TIMEOUT = 1800.0


@dataclass(frozen=True)
class Step:
    kind: str  # "unit" | "build" | "verify-images"
    name: str

    def label(self) -> str:
        return self.name if self.kind == "unit" else f"{self.kind} {self.name}"


@dataclass
class Options:
    allow_destroy: bool = False
    timeout: float = DEFAULT_TIMEOUT
    dry_run: bool = False
    cicd_dir: str | None = None
    skip_tests: bool = False
    tag: str | None = None
    progress: Callable[[str], None] = field(default=lambda msg: print(msg, file=sys.stderr, flush=True))


# ------------------------------------------------------------------------------------------
# order
# ------------------------------------------------------------------------------------------


def workload_order(root: Path, env: str) -> list[str]:
    """Enabled layer-5 units in Terragrunt ``run --all`` order, ``iap-egress`` last.

    Terragrunt groups units by dependency depth (a unit runs after all its dependencies) and sorts
    each group by path; excluded units (``env.yaml`` gateways / governance) are skipped.
    """
    units = [u for u in repo.units(root, env) if u.startswith(f"{WORKLOADS}/") and infra.unit_enabled(root, env, u)]
    inside = set(units)
    deps = {u: [d for d in infra.unit_dependencies(root, env, u) if d in inside and d != u] for u in units}
    depth: dict[str, int] = {}

    def level(unit: str, seen: tuple[str, ...] = ()) -> int:
        if unit in depth:
            return depth[unit]
        if unit in seen:
            raise OpsError(f"Dependency cycle in {env}: {' -> '.join((*seen, unit))}")
        depth[unit] = 1 + max((level(d, (*seen, unit)) for d in deps[unit]), default=-1)
        return depth[unit]

    ordered = sorted(units, key=lambda u: (level(u), u))
    if FINAL_UNIT in ordered:
        ordered.remove(FINAL_UNIT)
        ordered.append(FINAL_UNIT)
    return ordered


def layer_steps(root: Path, env: str, layer: int) -> list[Step]:
    if layer == 0:
        return [Step("unit", infra.CICD_UNIT)]
    if layer in FOUNDATIONS:
        return [Step("unit", FOUNDATIONS[layer])]
    if layer == 5:
        return [*(Step("unit", u) for u in workload_order(root, env)), Step("verify-images", env)]
    raise OpsError(f"Unknown layer {layer}: use 0..5")


def all_steps(root: Path, env: str, *, skip_build: bool = False, skip_verify: bool = False) -> list[Step]:
    """``make deploy-all``: layer 0 -> 1-3 -> 4 -> images -> 5 (+ the running-image check)."""
    steps = [step for layer in range(0, 5) for step in layer_steps(root, env, layer)]
    if not skip_build:
        steps.append(Step("build", "images"))
    steps += [s for s in layer_steps(root, env, 5) if not (skip_verify and s.kind == "verify-images")]
    return steps


def unit_steps(root: Path, env: str, names: list[str]) -> list[Step]:
    """Named units, resolved, de-duplicated and sorted into deploy order."""
    resolved: list[str] = []
    for name in names:
        unit, _ = infra.locate_unit(root, env, name)
        if unit != infra.CICD_UNIT and not infra.unit_enabled(root, env, unit):
            raise OpsError(f"Unit {unit} is excluded in {env}/env.yaml (gateways / governance): enable it first")
        if unit not in resolved:
            resolved.append(unit)
    order = [infra.CICD_UNIT, *FOUNDATIONS.values(), *workload_order(root, env)]
    rank = {u: i for i, u in enumerate(order)}
    resolved.sort(key=lambda u: rank.get(u, len(order)))
    return [Step("unit", u) for u in resolved]


def plan_steps(
    root: Path,
    env: str,
    *,
    layers: list[int] | None = None,
    units: list[str] | None = None,
    all_layers: bool = False,
    skip_build: bool = False,
    skip_verify: bool = False,
) -> list[Step]:
    choices = sum(1 for chosen in (layers, units, all_layers) if chosen)
    if choices != 1:
        raise OpsError("Choose what to deploy: <unit> ..., --layer N (repeatable) or --all")
    repo.env_dir(root, env)
    if all_layers:
        return all_steps(root, env, skip_build=skip_build, skip_verify=skip_verify)
    if layers:
        steps = [s for layer in sorted(set(layers)) for s in layer_steps(root, env, layer)]
        return [s for s in steps if not (skip_verify and s.kind == "verify-images")]
    return unit_steps(root, env, units or [])


# ------------------------------------------------------------------------------------------
# run
# ------------------------------------------------------------------------------------------


def _cicd_directory(root: Path, step: Step, opts: Options) -> Path | None:
    if step.name != infra.CICD_UNIT:
        return None
    location, from_main = build.resolve_cicd_dir(root, opts.cicd_dir)
    if from_main and not opts.dry_run:
        raise OpsError(
            f"Layer-0 state is in the main checkout ({location.parents[3]}), not in this worktree: deploy layer 0 "
            "there, or pass --cicd-dir explicitly."
        )
    return location


def _unit(root: Path, env: str, step: Step, opts: Options) -> dict[str, Any]:
    directory = _cicd_directory(root, step, opts)
    if opts.dry_run:
        planned = infra.plan_unit(root, env, step.name, timeout=opts.timeout, directory=directory)
        counts = planned.get("counts") or {}
        destructive = int(counts.get("destroy", 0)) + int(counts.get("replace", 0))
        return {
            **planned,
            "status": "planned",
            "would_refuse": destructive > 0 and not opts.allow_destroy,
        }
    applied = infra.apply_unit(
        root, env, step.name, allow_destroy=opts.allow_destroy, timeout=opts.timeout, directory=directory
    )
    return {**applied, "status": "refused" if applied.get("refused") else "applied"}


def _build(root: Path, step: Step, opts: Options) -> dict[str, Any]:
    result = build.build_images(
        root, step.name, tag=opts.tag, cicd_dir=opts.cicd_dir, skip_tests=opts.skip_tests, dry_run=opts.dry_run
    )
    status = "dry-run" if opts.dry_run else ("built" if result.get("ok") else "failed")
    return {**result, "status": status}


def _verify(root: Path, step: Step, opts: Options) -> dict[str, Any]:
    cmd = release.verify_images_cmd(step.name)
    if opts.dry_run:
        return {"status": "dry-run", "commands": [" ".join(cmd)]}
    result = release.verify_images(root, step.name, cicd_dir=opts.cicd_dir, echo=opts.progress)
    return {
        "status": "passed" if result["ok"] else "failed",
        "exit_code": result["exit_code"],
        "workloads": result["workloads"],
    }


def deploy(root: Path, env: str, steps: list[Step], opts: Options) -> dict[str, Any]:
    """Runs ``steps`` in order; stops at the first failure or refusal. Never raises for a step failure."""
    results: list[dict[str, Any]] = []
    exit_code = 0
    started = time.monotonic()
    for index, step in enumerate(steps, 1):
        verb = "plan" if opts.dry_run and step.kind == "unit" else ("apply" if step.kind == "unit" else step.kind)
        opts.progress(
            f"▶ [{index}/{len(steps)}] {verb} {env}/{step.name}"
            if step.kind == "unit"
            else f"▶ [{index}/{len(steps)}] {step.label()}"
        )
        step_started = time.monotonic()
        try:
            if step.kind == "unit":
                outcome = _unit(root, env, step, opts)
            elif step.kind == "build":
                outcome = _build(root, step, opts)
            else:
                outcome = _verify(root, step, opts)
        except OpsError as exc:
            outcome = {"status": "failed", "error": str(exc)}
        outcome = {"step": step.name, "kind": step.kind, **outcome}
        outcome.setdefault("seconds", round(time.monotonic() - step_started, 1))
        results.append(outcome)
        if outcome["status"] in ("failed", "refused"):
            exit_code = 2 if outcome["status"] == "refused" else int(outcome.get("exit_code") or 1)
            break
    pending = [{"step": s.name, "kind": s.kind, "status": "pending"} for s in steps[len(results) :]]
    return {
        "env": env,
        "dry_run": opts.dry_run,
        "allow_destroy": opts.allow_destroy,
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "steps": results + pending,
        "seconds": round(time.monotonic() - started, 1),
    }


# ------------------------------------------------------------------------------------------
# destroy (dev only)
# ------------------------------------------------------------------------------------------

PROTECTED_ENVS = ("prd",)


def destroy_order(root: Path, env: str) -> list[str]:
    """Layer 5 (reverse deploy order) -> 4 -> 3 -> 2 -> 1. Never the shared layer 0."""
    return [*reversed(workload_order(root, env)), *(FOUNDATIONS[layer] for layer in (4, 3, 2, 1))]


def _deployed(root: Path, env: str, unit: str) -> bool | None:
    if repo.backend(root, env) != "local":
        return None
    return repo.state_path(root, env, unit).is_file() and bool(repo.unit_state(root, env, unit).resources)


def destroy(
    root: Path,
    env: str,
    *,
    confirm: str | None,
    dry_run: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    progress: Callable[[str], None] = Options().progress,
) -> dict[str, Any]:
    """Destroys ``env`` layers 5 -> 1, one unit at a time (``terragrunt destroy``); refuses ``prd``.

    ``confirm`` must equal ``env`` (the Makefile asked to type the env name). With the local
    backend, units without state are skipped. Stops at the first failure.
    """
    if env in PROTECTED_ENVS:
        raise OpsError(f"destroy refuses --env {env}.")
    repo.env_dir(root, env)
    if not dry_run and confirm != env:
        raise OpsError(f"destroy needs --confirm {env} (the environment name) to destroy {env}.")
    order = destroy_order(root, env)
    results: list[dict[str, Any]] = []
    exit_code = 0
    for index, unit in enumerate(order, 1):
        deployed = _deployed(root, env, unit)
        if deployed is False:
            results.append({"step": unit, "kind": "unit", "status": "skipped", "reason": "no state"})
            continue
        if dry_run:
            results.append({"step": unit, "kind": "unit", "status": "dry-run"})
            continue
        progress(f"▶ [{index}/{len(order)}] destroy {env}/{unit}")
        started = time.monotonic()
        try:
            done = repo.run(
                repo.terragrunt_cmd("destroy", "-auto-approve", "-input=false", "-no-color"),
                cwd=repo.unit_dir(root, env, unit),
                timeout=timeout,
                what=f"terragrunt destroy ({unit})",
            )
            if done.returncode != 0:
                raise repo.failure(f"terragrunt destroy ({unit})", done)
            results.append(
                {"step": unit, "kind": "unit", "status": "destroyed", "seconds": round(time.monotonic() - started, 1)}
            )
        except OpsError as exc:
            results.append({"step": unit, "kind": "unit", "status": "failed", "error": str(exc)})
            exit_code = 1
            break
    pending = [{"step": u, "kind": "unit", "status": "pending"} for u in order[len(results) :]]
    return {
        "env": env,
        "dry_run": dry_run,
        "destroy": True,
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "steps": results + pending,
    }
