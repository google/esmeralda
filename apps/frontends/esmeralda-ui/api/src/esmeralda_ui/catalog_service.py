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

"""The service catalog, read through ``esmeralda.catalog`` (no second implementation).

The image bakes ``catalog/`` (sources plus ``catalog/.resolved/<env>.json``) and
``contracts/catalog.schema.json`` at build time. The BFF resolves the sources with the same resolver
the CLI and Terraform use and serves :func:`esmeralda.catalog.show` (who may call each target,
through which gateway, who pays). It fails closed: if the committed resolved file does not match the
sources (the same check as ``esmeralda catalog resolve --check``), the catalog endpoints answer 503
instead of serving something Terraform didn't deploy. There is no synthetic fallback.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from typing import Any

from esmeralda import catalog

from esmeralda_ui.models import Catalog, CatalogCaller, CatalogPolicy, CatalogTarget, CatalogTeam, Payer

log = logging.getLogger(__name__)


class CatalogUnavailable(Exception):
    """The catalog can't be served (missing, invalid, or out of date with its sources)."""


def _ingress(root: Path, env: str) -> dict[str, Any]:
    """env.yaml's ``gateways`` block when the checkout is present (local runs); empty in the image."""
    if not (root / "infrastructure" / "live" / env / "env.yaml").is_file():
        return {}
    try:
        from esmeralda.ops import repo

        return repo.gateways_block(root, env)
    except Exception:  # noqa: BLE001 - optional enrichment only
        return {}


def _caller(entry: dict[str, Any]) -> CatalogCaller:
    pays = entry.get("pays") or {}
    tools = entry.get("tools")
    return CatalogCaller(
        caller=str(entry["caller"]),
        identity=str(entry.get("identity") or ""),
        team=entry.get("team"),
        gateway=entry.get("gateway"),
        pays=Payer(team=pays.get("team"), cost_centers=[str(c) for c in pays.get("cost_centers") or []]),
        enforced=bool(entry.get("enforced")),
        tools=[str(t) for t in tools] if isinstance(tools, list) else None,
        product=str(entry["product"]) if entry.get("product") else None,
    )


def build_catalog(root: Path, env: str) -> Catalog:
    """Resolve ``root``'s catalog for ``env``, check it against the committed resolved file, and shape it."""
    try:
        resolved = catalog.resolve(root, env)
        path = catalog.resolved_path(root, env)
        committed = path.read_text() if path.is_file() else None
    except (catalog.CatalogError, OSError) as exc:
        raise CatalogUnavailable(f"catalog for {env} could not be resolved: {exc}") from exc
    if committed is None:
        raise CatalogUnavailable(f"{path} is missing")
    if committed != catalog.render_resolved_file(resolved):
        raise CatalogUnavailable(f"{path} is out of date with the catalog sources")

    shown = catalog.show(resolved, ingress=_ingress(root, env))
    targets = []
    for row in shown["targets"]:
        source = resolved.targets.get(row["target"], {})
        targets.append(
            CatalogTarget(
                name=row["target"],
                kind=row["kind"],
                owner_team=row["owner_team"],
                description=source.get("description"),
                example=bool(row.get("example")),
                deploy=row.get("deploy"),
                compiled=bool(row.get("compiled")),
                registry=row.get("registry"),
                gateway=row.get("gateway"),
                ingress_backend=row.get("ingress_backend"),
                google_apis=row.get("google_apis"),
                callers=[_caller(c) for c in row.get("callers") or []],
            )
        )
    teams = [
        CatalogTeam(
            name=name,
            cost_centers=[str(c) for c in (team or {}).get("cost_centers") or []],
            external=bool((team or {}).get("external")),
        )
        for name, team in sorted(resolved.teams.items())
    ]
    policy = shown.get("policy") or {}
    return Catalog(
        environment=env,
        resolved_file=path.relative_to(root).as_posix(),
        resolved_sha256=hashlib.sha256(committed.encode()).hexdigest(),
        policy=CatalogPolicy(
            unmapped_caller=policy.get("unmapped_caller"), declared_mismatch=policy.get("declared_mismatch")
        ),
        teams=teams,
        targets=targets,
    )


class CatalogService:
    """Builds the catalog once (it only changes with a new image) and caches it, or the failure."""

    def __init__(self, root: Path | None, env: str):
        self._root = root
        self._env = env
        self._lock = threading.Lock()
        self._catalog: Catalog | None = None

    def get(self) -> Catalog:
        with self._lock:
            if self._catalog is None:
                if self._root is None:
                    raise CatalogUnavailable("no catalog root configured (ESMERALDA_UI_CATALOG_ROOT)")
                self._catalog = build_catalog(self._root, self._env)
            return self._catalog

    def target(self, name: str) -> CatalogTarget | None:
        return next((t for t in self.get().targets if t.name == name), None)
