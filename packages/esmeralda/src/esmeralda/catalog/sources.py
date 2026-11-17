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

"""Catalog sources: where catalog entries come from (``catalog/sources.yaml``).

Each source returns a :class:`Layer` (teams, callers, targets, team profiles, policy, inventory).
The resolver merges the layers in the order ``sources.yaml`` lists them (see
:mod:`esmeralda.catalog.merge`). Implemented:

* ``files``: YAML documents under ``catalog/`` (the demo default, owned per team via CODEOWNERS).
  It must be the first enabled source, and it is the only one that may set authorization data.
* ``project_labels`` (:mod:`esmeralda.catalog.adapters.project_labels`): team attribution from
  project labels, read from a snapshot.

Extension points (registered, not implemented yet):

* ``agent_registry``: inventory of agents / MCP servers / endpoints from Agent Registry.

Adapters for other systems (Backstage, a CMDB, ...) subclass :class:`Source` and call
:func:`register_source`; adapters that read a live system subclass
:class:`esmeralda.catalog.snapshot.SnapshotSource` instead, so that ``esmeralda catalog fetch``
writes a reviewed snapshot and resolving reads only that snapshot (no network). Discovery never
grants: a non-``files`` source may only contribute
inventory (``Layer.inventory``) and attribution metadata, within the capabilities of its type
(``CAPABILITIES``, capped in code by :data:`esmeralda.catalog.merge.MAX_EXTERNAL_CAPABILITIES`).
Callers, grants, identities, policy, teams and their cost centres stay in catalog files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from esmeralda.catalog.schema import CatalogError, check_header, load_yaml, validate

SECTIONS = ("teams", "callers", "targets", "profiles")
# Sections a layer may hold besides the policy: SECTIONS plus discovered (never compiled) inventory.
LAYER_SECTIONS = (*SECTIONS, "inventory")


@dataclass
class Layer:
    """Entries contributed by one source, with where each one came from (for errors and provenance).

    ``inventory`` holds what an external source discovered, keyed by a deterministic name; a catalog
    file adopts an inventory entry only through an explicit pin (``deploy.registry``).
    """

    source: str
    teams: dict[str, dict[str, Any]] = field(default_factory=dict)
    callers: dict[str, dict[str, Any]] = field(default_factory=dict)
    targets: dict[str, dict[str, Any]] = field(default_factory=dict)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    inventory: dict[str, dict[str, Any]] = field(default_factory=dict)
    origins: dict[tuple[str, str], str] = field(default_factory=dict)
    # Non-fatal notes for the user (for example an optional source whose snapshot is missing).
    warnings: list[str] = field(default_factory=list)

    def section(self, name: str) -> dict[str, dict[str, Any]]:
        return getattr(self, name)


class Source:
    """A catalog source. Subclasses implement :meth:`load`.

    ``CAPABILITIES`` (``{section: {field path, ...}}``, ``"*"`` for whole inventory entries) is what
    an adapter of a type without a built-in allowlist may contribute; ``None`` means nothing. It can
    never exceed :data:`esmeralda.catalog.merge.MAX_EXTERNAL_CAPABILITIES` (no authorization data),
    and ``options.fields`` in sources.yaml may only narrow it.
    """

    type_name = ""
    CAPABILITIES: dict[str, Any] | None = None

    def __init__(self, *, name: str, catalog_dir: Path, root: Path, schema: dict[str, Any], spec: dict[str, Any],
                 env: str | None = None):
        self.name = name
        self.catalog_dir = catalog_dir
        self.root = root
        self.schema = schema
        self.spec = spec
        self.env = env  # the environment being resolved (snapshot sources read catalog/snapshots/<name>/<env>.json)

    def load(self) -> Layer:  # pragma: no cover - interface
        raise NotImplementedError

    def complete(self, layer: Layer, merged: Layer) -> Layer:
        """Hook called at merge time with a copy of what the earlier sources merged (``merged``).

        A source whose contributions depend on catalog entries (for example project labels, which
        fill ``owner_team`` only on inventory entries an earlier source reported and only for teams
        the catalog files declare) returns them here. The result goes through the same capability
        checks as :meth:`load`'s layer. Default: ``layer`` unchanged.
        """
        return layer

    def findings(self, document: dict[str, Any]) -> list[dict[str, str]]:
        """Hook: where this source's data disagrees with the resolved catalog (after the environment
        overrides), as ``{"kind", "subject", "detail"}`` rows. ``policy.attribution.label_mismatch``
        decides whether they fail the resolve (``fail``) or are warnings (``warn``); ``esmeralda
        catalog diff`` always lists them. Default: none.
        """
        return []


SOURCE_TYPES: dict[str, type[Source]] = {}


def register_source(type_name: str) -> Callable[[type[Source]], type[Source]]:
    """Class decorator: make a :class:`Source` available as ``type: <type_name>`` in sources.yaml.

    ``files`` is reserved for the built-in :class:`FilesSource`: it is the only source type that may
    set authorization data, so an adapter can't take it over.
    """

    def wrap(cls: type[Source]) -> type[Source]:
        if type_name == "files" and "files" in SOURCE_TYPES and SOURCE_TYPES["files"] is not cls:
            raise CatalogError("source type 'files' is reserved for the built-in catalog files source")
        cls.type_name = type_name
        SOURCE_TYPES[type_name] = cls
        return cls

    return wrap


def _merge_policy(into: dict[str, Any], new: dict[str, Any], label: str, path: str = "policy") -> list[str]:
    """Merge policy fragments from several files; setting the same leaf twice is a duplicate."""
    problems = []
    for key, value in new.items():
        here = f"{path}.{key}"
        if key not in into:
            into[key] = value
        elif isinstance(into[key], dict) and isinstance(value, dict):
            problems += _merge_policy(into[key], value, label, here)
        else:
            problems.append(f"{label}: duplicate {here} (already set by another catalog file)")
    return problems


@register_source("files")
class FilesSource(Source):
    """YAML files under the catalog directory: one target per file, or maps of teams/callers/targets/policy."""

    SKIP = {"sources.yaml"}

    def _files(self) -> list[Path]:
        paths = self.spec.get("paths") or ["."]
        skip_dirs = {self.catalog_dir / d for d in (".resolved", "environments")}
        found: list[Path] = []
        for rel in paths:
            base = (self.catalog_dir / rel).resolve()
            if not base.exists():
                raise CatalogError(f"sources.yaml: {self.name}: path '{rel}' does not exist under catalog/")
            if base.is_file():
                found.append(base)
                continue
            for path in sorted(base.rglob("*")):
                if path.suffix not in (".yaml", ".yml") or path.name in self.SKIP or not path.is_file():
                    continue
                if any(d.resolve() in path.parents for d in skip_dirs):
                    continue
                found.append(path)
        unique = list(dict.fromkeys(found))
        return unique

    def load(self) -> Layer:
        layer = Layer(source=self.name)
        problems: list[str] = []
        for path in self._files():
            label = path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else str(path)
            try:
                doc = load_yaml(path, label)
                check_header(doc, label)
            except CatalogError as exc:
                problems += exc.problems
                continue
            problems += self._add_document(layer, doc, label)
        if problems:
            raise CatalogError(problems)
        return layer

    def _add(self, layer: Layer, section: str, name: str, value: dict[str, Any], label: str) -> list[str]:
        entries = layer.section(section)
        if name in entries:
            kind = section[:-1]
            return [f"{label}: duplicate {kind} '{name}' (also defined in {layer.origins[(section, name)]})"]
        entries[name] = value
        layer.origins[(section, name)] = label
        return []

    def _add_document(self, layer: Layer, doc: dict[str, Any], label: str) -> list[str]:
        if "environment" in doc or "sources" in doc:
            return [f"{label}: environment overrides and sources.yaml documents are not catalog entries"]
        if "name" in doc:  # one target per file
            problems = validate(self.schema, "target_document", doc, label)
            if problems:
                return problems
            body = {k: v for k, v in doc.items() if k not in ("schema", "schema_version", "name")}
            return self._add(layer, "targets", doc["name"], body, label)
        if "profile" in doc:
            problems = validate(self.schema, "team_profile_document", doc, label)
            if problems:
                return problems
            profile = doc["profile"]
            return self._add(layer, "profiles", profile["team"], profile, label)
        problems = validate(self.schema, "collection_document", doc, label)
        if problems:
            return problems
        for section in ("teams", "callers", "targets"):
            for name, value in (doc.get(section) or {}).items():
                problems += self._add(layer, section, name, value, label)
        if "policy" in doc:
            problems += _merge_policy(layer.policy, doc["policy"], label)
        return problems


class _NotImplementedSource(Source):
    phase = ""

    def load(self) -> Layer:
        raise NotImplementedError(
            f"catalog source type '{self.type_name}' ({self.name}) is an extension point and is not implemented "
            f"yet{self.phase}. Set 'enabled: false' for it in catalog/sources.yaml, or register an adapter with "
            "esmeralda.catalog.register_source."
        )


@register_source("agent_registry")
class AgentRegistrySource(_NotImplementedSource):
    """Inventory (agents, MCP servers, endpoints) from Agent Registry. Planned: report each registry
    entry as inventory; a catalog file adopts one only with a ``deploy.registry`` pin."""

    phase = " (planned with the catalog sources phase)"


def source_name(spec: dict[str, Any], index: int) -> str:
    return spec.get("name") or f"{spec['type']}-{index}"


def build_source(root: Path, catalog_dir: Path, schema: dict[str, Any], spec: dict[str, Any], index: int,
                 env: str | None = None) -> Source:
    """The :class:`Source` for one sources.yaml entry (enabled or not)."""
    type_name = spec["type"]
    cls = SOURCE_TYPES.get(type_name)
    if cls is None:
        known = ", ".join(sorted(SOURCE_TYPES))
        raise CatalogError(f"catalog/sources.yaml: sources[{index}]: unknown source type '{type_name}' "
                           f"(known: {known})")
    return cls(name=source_name(spec, index), catalog_dir=catalog_dir, root=root, schema=schema, spec=spec, env=env)


def build_sources(root: Path, catalog_dir: Path, schema: dict[str, Any], sources_doc: dict[str, Any],
                  env: str | None = None) -> list[Source]:
    built = [
        build_source(root, catalog_dir, schema, spec, i, env)
        for i, spec in enumerate(sources_doc["sources"])
        if spec.get("enabled", True) is not False
    ]
    if not built:
        raise CatalogError("catalog/sources.yaml: no enabled source")
    return built
