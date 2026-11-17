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

"""Release promotion by digest and the running-image check.

* ``status`` shows ``release/<env>.yaml`` and the shared dev and release repositories.
* ``promote`` copies a verified dev build into the immutable release repository, by digest:

  1. resolve the digest of every image tagged ``<source>`` (``dev-<gitsha>``) in the dev repository;
  2. check every ``<version>`` tag in the release repository first, so a promotion is all-or-nothing
     (an existing tag with a different digest is refused; the same digest is a no-op);
  3. ``gcloud container images add-tag`` each digest as ``<version>``, then re-check it;
  4. write ``release/<env>.yaml`` (image => digest, source commit, verification).

  It never deploys and never edits env.yaml: merge the manifest by PR, then deploy.
* ``verify_images`` checks every Layer 5 workload runs the image it should. Expected: the digest in
  ``release/<env>.yaml`` when it exists, otherwise the digest of ``<repository>/<image>:<container_tag>``.
  Running: the digest of every Cloud Run revision serving traffic, or the Agent Runtime engine's
  ``spec.containerSpec.imageUri``. Runs at the end of every ``esmeralda deploy --layer 5``.

The image list is ``build.image_names()`` (what ``esmeralda build images`` builds); ``WORKLOADS``
maps each image to the workload that runs it. The repositories come from the layer-0 outputs
(``build.resolve_cicd_dir``: a worktree without layer-0 state reads the main checkout's).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from esmeralda.ops import build, repo
from esmeralda.ops import release_manifest as manifest
from esmeralda.ops.repo import OpsError

Echo = Callable[[str], None] | None

SOURCE_RE = re.compile(r"^dev-([0-9a-f]{7,40})$")
DIGEST_RE = manifest.DIGEST_RE
RULE = "=" * 72
THIN_RULE = "-" * 72
GCLOUD_TIMEOUT = 120.0
DIGEST_FORMAT = "value(image_summary.digest)"


@dataclass(frozen=True)
class Workload:
    """A Layer 5 workload and the image it runs.

    ``kind`` is ``cloud_run`` (service ``<name>-<env>`` in the project from the layer-1 output
    ``project_output``) or ``engine`` (Agent Runtime engine from the ``engine_id`` output of
    ``layer-5-workloads/agents/<name>``). ``gateway`` names the gateway adapter that must be in
    ``gateways.enabled`` for the workload to exist.
    """

    name: str
    image: str
    kind: str
    project_output: str = ""
    gateway: str = ""

    def label(self, env: str) -> str:
        return f"{self.name}-{env}" if self.kind == "cloud_run" else self.name


WORKLOADS = (
    Workload("corporate-email", "corporate-email", "cloud_run", "mcps_project_id"),
    Workload("income-verification", "income-verification-api", "cloud_run", "mcps_project_id"),
    Workload("legacy-dms", "legacy-dms", "cloud_run", "mcps_project_id"),
    Workload("envoy-gateway", "envoy-gateway", "cloud_run", "gateway_project_id", gateway="envoy"),
    Workload("cx-mortgage-orchestrator", "cx-mortgage-orchestrator", "engine"),
    Workload("ai-coe-mortgage-specialist", "ai-coe-mortgage-specialist", "engine"),
)


def images() -> list[str]:
    """Every Layer 5 image, as built by ``esmeralda build images`` (one list for build and release)."""
    return build.image_names()


def manifest_path(root: Path, env: str) -> Path:
    return root / "release" / f"{env}.yaml"


def _echo(echo: Echo, line: str) -> None:
    if echo:
        echo(line)


# ------------------------------------------------------------------------------------------
# subprocesses (one seam for the tests)
# ------------------------------------------------------------------------------------------


def _run(cmd: list[str], root: Path, *, timeout: float = GCLOUD_TIMEOUT) -> subprocess.CompletedProcess[str]:
    return repo.run(cmd, cwd=root, timeout=timeout, what=cmd[0])


def _http_json(url: str, token: str, *, timeout: float = 60.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed https URL)
        return json.loads(response.read().decode() or "{}")


def image_digest(root: Path, ref: str) -> str:
    """The ``sha256:`` digest of ``<repository>/<image>:<tag>``, or "" if it does not exist."""
    result = _run(["gcloud", "artifacts", "docker", "images", "describe", ref, f"--format={DIGEST_FORMAT}"], root)
    digest = result.stdout.strip() if result.returncode == 0 else ""
    return digest if DIGEST_RE.match(digest) else ""


def repositories(root: Path, cicd_dir: str | None = None) -> dict[str, str]:
    """``{"dev": url, "release": url}`` from the layer-0 outputs."""
    location, _ = build.resolve_cicd_dir(root, cicd_dir)
    outputs = build.cicd_outputs(location)
    return {kind: str(outputs.get(f"{kind}_repository_url") or "") for kind in ("dev", "release")}


# ------------------------------------------------------------------------------------------
# status
# ------------------------------------------------------------------------------------------


def status(root: Path, env: str = "prd", *, cicd_dir: str | None = None) -> dict[str, Any]:
    """The release manifest of ``env`` (or None) and the shared repositories."""
    repo.env_dir(root, env)
    data = manifest.load(manifest_path(root, env))
    return {
        "env": env,
        "manifest": f"release/{env}.yaml" if data is not None else None,
        "release": data,
        "images": images(),
        "repositories": repositories(root, cicd_dir),
    }


def format_status(data: dict[str, Any]) -> str:
    env, rel, repos = data["env"], data["release"], data["repositories"]
    lines = [RULE, f"👑 ESMERALDA RELEASE STATUS ({env})", RULE]
    if rel is not None:
        verified = rel.get("verified") or {}
        lines += [
            f"• Manifest           : {data['manifest']}",
            f"• Version            : {rel.get('version', '')}",
            f"• Source             : {rel.get('source_tag', '')} ({rel.get('source_commit', '')})",
            f"• Promoted at        : {rel.get('promoted_at', '')}",
            f"• Verified           : {verified.get('environment', '')} {verified.get('date', '')} "
            f"{verified.get('result', '')}",
        ]
        digests = rel.get("images") or {}
        lines += [f"    {name:<28} {digests.get(name, '')}" for name in data["images"]]
    else:
        lines.append(f"• Manifest           : <none> (units fall back to container_tag in {env}/env.yaml)")
    lines += [
        f"• Dev repository     : {repos['dev']}  (mutable, every build)",
        f"• Release repository : {repos['release']}  (immutable, promoted by digest)",
        THIN_RULE,
        "Promote: esmeralda release promote --version vX.Y.Z --source dev-<sha> --verified YYYY-MM-DD",
        RULE,
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------
# promote
# ------------------------------------------------------------------------------------------


def _source_commit(root: Path, sha: str) -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return sha
    out = res.stdout.strip()
    return out if res.returncode == 0 and manifest.COMMIT_RE.match(out) else sha


def promote(
    root: Path,
    *,
    version: str,
    source: str,
    verified: str | None = None,
    env: str = "prd",
    dry_run: bool = False,
    cicd_dir: str | None = None,
    echo: Echo = print,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc),
) -> dict[str, Any]:
    """Promotes ``source`` (``dev-<gitsha>``) to ``version`` by digest and writes ``release/<env>.yaml``.

    All-or-nothing: every source digest and every existing release tag is checked before anything
    is copied. Raises OpsError on invalid input, a missing source image, a release tag that already
    holds another digest, or a copy that does not end up at the expected digest. ``dry_run`` checks
    everything but copies nothing and writes nothing.
    """
    repo.env_dir(root, env)
    if not manifest.VERSION_RE.match(version or ""):
        raise OpsError(f"--version must look like vX.Y.Z (got '{version}')")
    match = SOURCE_RE.match(source or "")
    if not match:
        raise OpsError(
            f"--source must be an immutable build tag dev-<gitsha> (got '{source}'); dev-latest moves on every build"
        )
    verified = verified or now().strftime("%Y-%m-%d")
    if not manifest.DATE_RE.match(verified):
        raise OpsError(f"--verified must be the date (YYYY-MM-DD) dev passed 'esmeralda verify --env dev' on {source}")
    source_commit = _source_commit(root, match.group(1))
    repos = repositories(root, cicd_dir)
    if not repos["dev"] or not repos["release"]:
        raise OpsError(
            "No dev/release repository in the layer-0 outputs (deploy layer 0, or pass --cicd-dir "
            f"<checkout with layer-0 state>/{build.CICD_UNIT.as_posix()})"
        )
    out = manifest_path(root, env)
    names = images()

    _echo(echo, RULE)
    _echo(echo, f"🚀 PROMOTING {source} -> {version} (copy by digest, manifest release/{env}.yaml)")
    _echo(echo, f"   from {repos['dev']}")
    _echo(echo, f"   to   {repos['release']}")
    if dry_run:
        _echo(echo, "   (dry run: nothing is copied or written)")
    _echo(echo, RULE)

    # 1. Resolve every source digest and check every release tag BEFORE copying anything.
    digests: dict[str, str] = {}
    already: set[str] = set()
    for name in names:
        digest = image_digest(root, f"{repos['dev']}/{name}:{source}")
        if not digest:
            raise OpsError(f"{name}:{source} not found in the dev repository")
        existing = image_digest(root, f"{repos['release']}/{name}:{version}")
        if existing and existing != digest:
            raise OpsError(
                f"{name}:{version} already exists in the release repository as {existing}, not {digest}. "
                "Release tags are immutable: bump the version."
            )
        if existing:
            already.add(name)
        digests[name] = digest
        _echo(echo, f"🔎 {name}:{source} = {digest}")

    # 2. Copy by digest (the release image is byte-identical to what was tested in dev).
    results = []
    for name in names:
        digest = digests[name]
        if name in already:
            _echo(echo, f"✔️  {name}:{version} already points at {digest} (no copy)")
            results.append({"image": name, "digest": digest, "status": "exists"})
            continue
        _echo(echo, f"📦 {name}@{digest} -> {name}:{version}")
        if dry_run:
            results.append({"image": name, "digest": digest, "status": "would-copy"})
            continue
        res = _run(
            [
                "gcloud",
                "container",
                "images",
                "add-tag",
                "--quiet",
                f"{repos['dev']}/{name}@{digest}",
                f"{repos['release']}/{name}:{version}",
            ],
            root,
        )
        if res.returncode != 0:
            raise repo.failure(f"gcloud container images add-tag {name}:{version}", res)
        copied = image_digest(root, f"{repos['release']}/{name}:{version}")
        if copied != digest:
            raise OpsError(f"{name}:{version} is {copied or 'missing'} after the copy, expected {digest}")
        results.append({"image": name, "digest": digest, "status": "copied"})

    # 3. The manifest (no registry URL or project id: units add the layer-0 release repository).
    text = manifest.render(
        version=version,
        source_tag=source,
        source_commit=source_commit,
        promoted_at=now(),
        verified_env="dev",
        verified_date=verified,
        verified_result="PASS",
        images=digests,
        repository="release",
    )
    previous = manifest.load(out)
    changed = _manifest_changes(previous, manifest.parse(text))
    data = {
        "env": env,
        "version": version,
        "source": source,
        "source_commit": source_commit,
        "verified": verified,
        "repositories": repos,
        "images": results,
        "manifest": f"release/{env}.yaml",
        "manifest_changes": changed,
        "dry_run": dry_run,
        "ok": True,
        "exit_code": 0,
    }
    if dry_run:
        summary = ", ".join(changed) if changed else "nothing apart from promoted_at"
        _echo(echo, f"📝 release/{env}.yaml would change: {summary}")
        _echo(echo, RULE)
        return data
    manifest.write(out, text)
    _echo(echo, f"wrote {out}")
    _echo(echo, "")
    _echo(echo, f"🎉 Promoted {version}. Nothing was deployed. Next:")
    _echo(echo, f"   1. open a PR with release/{env}.yaml and merge it")
    _echo(echo, f"   2. esmeralda deploy --layer 5 --env {env}   (it ends with esmeralda build --verify --env {env})")
    _echo(echo, "   Roll back = revert that PR and deploy again.")
    _echo(echo, RULE)
    return data


def _manifest_changes(old: dict | None, new: dict) -> list[str]:
    """Keys (``images.<name>`` for digests) that differ between two parsed manifests, ignoring promoted_at."""
    if old is None:
        return ["(new manifest)"]
    changes = []
    for key in sorted(set(old) | set(new)):
        if key in ("promoted_at", "images"):
            continue
        if old.get(key) != new.get(key):
            changes.append(key)
    old_images, new_images = old.get("images") or {}, new.get("images") or {}
    for name in sorted(set(old_images) | set(new_images)):
        if old_images.get(name) != new_images.get(name):
            changes.append(f"images.{name}")
    return changes


# ------------------------------------------------------------------------------------------
# verify_images
# ------------------------------------------------------------------------------------------


def verify_images_cmd(env: str) -> list[str]:
    """The command line of the running-image check (shown by dry runs)."""
    return ["esmeralda", "build", "--verify", "--env", env]


def _top_level(text: str, key: str) -> str:
    """A top-level ``  key = "value"`` of env.yaml's locals (two-space indent, as written)."""
    found = re.search(rf'^  {re.escape(key)}\s*=\s*"([^"]*)"', text, re.MULTILINE)
    return found.group(1) if found else ""


def _cloud_run_digest(root: Path, service: str, project: str, region: str) -> str:
    """The digest every revision serving traffic runs ("" if none; "mixed" if they differ)."""
    where = [f"--project={project}", f"--region={region}"]
    res = _run(["gcloud", "run", "services", "describe", service, *where, "--format=json"], root)
    if res.returncode != 0:
        return ""
    try:
        svc_status = json.loads(res.stdout or "{}").get("status") or {}
    except json.JSONDecodeError:
        return ""
    traffic = svc_status.get("traffic") or []
    revisions = [t["revisionName"] for t in traffic if t.get("percent") and t.get("revisionName")]
    if not revisions and svc_status.get("latestReadyRevisionName"):
        revisions = [svc_status["latestReadyRevisionName"]]
    running = ""
    for revision in dict.fromkeys(revisions):
        res = _run(
            ["gcloud", "run", "revisions", "describe", revision, *where, "--format=value(status.imageDigest)"], root
        )
        digest = res.stdout.strip().rsplit("@", 1)[-1] if res.returncode == 0 else ""
        running = "mixed" if running and running != digest else digest
    return running


def _engine_digest(engine: str, region: str, token: str) -> str:
    if not engine or not token:
        return ""
    try:
        data = _http_json(f"https://{region}-aiplatform.googleapis.com/v1beta1/{engine}", token)
    except (urllib.error.URLError, OSError, ValueError):
        return ""
    uri = ((data.get("spec") or {}).get("containerSpec") or {}).get("imageUri") or ""
    return uri.rsplit("@", 1)[-1] if "@" in uri else ""


def verify_images(root: Path, env: str, *, cicd_dir: str | None = None, echo: Echo = print) -> dict[str, Any]:
    """Compares the digest every Layer 5 workload runs with the expected one; ``echo`` streams the report."""
    repo.env_dir(root, env)
    text = repo.env_locals(root, env)
    region = _top_level(text, "region")
    tag = _top_level(text, "container_tag")
    repo_kind = _top_level(text, "image_repository") or "dev"
    enabled = set(repo.gateways_block(root, env).get("enabled") or [])
    rel = manifest.load(manifest_path(root, env))
    if rel is not None:
        expected_desc = f"release/{env}.yaml ({rel.get('version') or 'unknown version'})"
    else:
        expected_desc = f"tag :{tag}"
    _echo(echo, f"🔎 Verifying running images for {env} ({expected_desc})...")

    repo_url = ""
    if rel is None:
        repo_url = repositories(root, cicd_dir)["release" if repo_kind == "release" else "dev"]

    def expected(image: str) -> str:
        if rel is not None:  # every deployed image must be in the manifest; an unlisted one fails
            return (rel.get("images") or {}).get(image, "")
        return image_digest(root, f"{repo_url}/{image}:{tag}") if repo_url and tag else ""

    projects: dict[str, str] = {}
    token = ""
    if any(w.kind == "engine" for w in WORKLOADS):
        res = _run(["gcloud", "auth", "print-access-token"], root)
        token = res.stdout.strip() if res.returncode == 0 else ""

    workloads = []
    for w in WORKLOADS:
        if w.gateway and w.gateway not in enabled:
            continue
        want = expected(w.image)
        if w.kind == "cloud_run":
            if w.project_output not in projects:
                projects[w.project_output] = str(repo.output(root, env, "layer-1-projects", w.project_output) or "")
            running = _cloud_run_digest(root, w.label(env), projects[w.project_output], region)
        else:
            engine = repo.output(root, env, f"layer-5-workloads/agents/{w.name}", "engine_id") or ""
            running = _engine_digest(str(engine), region, token)
        ok = bool(want) and want == running
        item = {"workload": w.label(env), "image": w.image, "expected": want, "running": running, "ok": ok}
        workloads.append(item)
        if ok:
            _echo(echo, f"  ✅ {item['workload']:<28} {running[7:19]}")
        else:
            _echo(
                echo,
                f"  ❌ {item['workload']:<28} running {running or 'none'}, expected {want or '<not listed>'} "
                f"({expected_desc})",
            )

    failures = sum(1 for item in workloads if not item["ok"])
    if failures:
        _echo(
            echo,
            f"❌ {failures} workload(s) are not running {expected_desc}. "
            f"Re-run 'esmeralda deploy --layer 5 --env {env}'.",
        )
    else:
        _echo(echo, f"✅ All workloads run {expected_desc}.")
    return {
        "env": env,
        "expected_from": expected_desc,
        "manifest": f"release/{env}.yaml" if rel is not None else None,
        "workloads": workloads,
        "failures": failures,
        "ok": failures == 0,
        "exit_code": 1 if failures else 0,
    }
