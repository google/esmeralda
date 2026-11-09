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

Each source returns a :class:`Layer` (teams, callers, targets, team profiles, policy). The resolver
merges the layers in the order ``sources.yaml`` lists them. Implemented:

* ``files``: YAML documents under ``catalog/`` (the demo default, owned per team via CODEOWNERS).

Extension points (registered, not implemented yet):

* ``agent_registry``: inventory of agents / MCP servers / endpoints from Agent Registry.
* ``project_labels``: team attribution from project labels.

Adapters for other systems (Backstage, a CMDB, ...) subclass :class:`Source` and call
:func:`register_source`; they only have to return a :class:`Layer` whose entries satisfy
``contracts/catalog.schema.json``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from esmeralda.catalog.schema import CatalogError, check_header, load_yaml, validate

SECTIONS = ("teams", "callers", "targets", "profiles")


@dataclass
class Layer:
    """Entries contributed by one source, with where each one came from (for error messages)."""

    source: str
    teams: dict[str, dict[str, Any]] = field(default_factory=dict)
    callers: dict[str, dict[str, Any]] = field(default_factory=dict)
    targets: dict[str, dict[str, Any]] = field(default_factory=dict)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    origins: dict[tuple[str, str], str] = field(default_factory=dict)

    def section(self, name: str) -> dict[str, dict[str, Any]]:
        return getattr(self, name)


class Source:
    """A catalog source. Subclasses implement :meth:`load`."""

    type_name = ""

    def __init__(self, *, name: str, catalog_dir: Path, root: Path, schema: dict[str, Any], spec: dict[str, Any]):
        self.name = name
        self.catalog_dir = catalog_dir
        self.root = root
        self.schema = schema
        self.spec = spec

    def load(self) -> Layer:  # pragma: no cover - interface
        raise NotImplementedError


SOURCE_TYPES: dict[str, type[Source]] = {}


def register_source(type_name: str) -> Callable[[type[Source]], type[Source]]:
    """Class decorator: make a :class:`Source` available as ``type: <type_name>`` in sources.yaml."""

    def wrap(cls: type[Source]) -> type[Source]:
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
    """Inventory (agents, MCP servers, endpoints) from Agent Registry. Planned: map each registry entry
    to a target with ``deploy.managed: false`` + ``endpoint`` unless a files entry already deploys it."""

    phase = " (planned with the catalog sources phase)"


@register_source("project_labels")
class ProjectLabelsSource(_NotImplementedSource):
    """Team attribution from project labels (e.g. ``team``, ``cost-center``). Planned: fill ``owner_team``
    and team ``cost_centers`` that the files source leaves unset."""

    phase = " (planned with the catalog sources phase)"


def build_sources(root: Path, catalog_dir: Path, schema: dict[str, Any], sources_doc: dict[str, Any]) -> list[Source]:
    built = []
    for i, spec in enumerate(sources_doc["sources"]):
        if spec.get("enabled", True) is False:
            continue
        type_name = spec["type"]
        cls = SOURCE_TYPES.get(type_name)
        if cls is None:
            known = ", ".join(sorted(SOURCE_TYPES))
            raise CatalogError(
                f"catalog/sources.yaml: sources[{i}]: unknown source type '{type_name}' (known: {known})"
            )
        name = spec.get("name") or f"{type_name}-{i}"
        built.append(cls(name=name, catalog_dir=catalog_dir, root=root, schema=schema, spec=spec))
    if not built:
        raise CatalogError("catalog/sources.yaml: no enabled source")
    return built
