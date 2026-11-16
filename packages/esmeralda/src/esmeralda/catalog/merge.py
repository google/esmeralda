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

"""Merging catalog source layers: capabilities, field strategies, adoption and provenance.

Discovery never grants; adoption is a repo review:

* The first enabled source is ``files`` (catalog YAML, CODEOWNERS-reviewed). It is the only source
  that may set authorization data: target callers and grants, the ``callers`` section, identities,
  ``policy``, teams and their cost centres, ``google_apis``, ``user_credential``, ``deploy.unit``,
  ``serving``, ``models``, ``gateway``, ``observability``.
* Any other source contributes only what the capability allowlist of its type permits
  (:data:`SOURCE_CAPABILITIES`, capped by :data:`MAX_EXTERNAL_CAPABILITIES`). Anything else fails
  the resolve with a message naming the source and the field. ``options.fields`` in sources.yaml may
  narrow a source, never widen it.
* External sources report what they discovered as ``inventory`` (never compiled). A catalog file
  adopts an inventory entry only through an explicit pin, ``deploy.registry: {source, id}``, never by
  name; the adopted target then takes the inventory fields its source may fill, per field strategy:
  ``strict`` (every contributor must agree, otherwise an error naming both) or ``first`` (the earlier
  contributor wins, files first; the loser is recorded as ``shadowed``).
* Sources merge in sources.yaml order and entries in sorted ``(section, name)`` order, so the result
  doesn't depend on adapter dict order, filesystem order or hash seeds.
* Every entry gets entry-level provenance; fields contributed by an external source (or shadowed)
  get field-level provenance.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from esmeralda.catalog.schema import CatalogError
from esmeralda.catalog.sources import LAYER_SECTIONS, SECTIONS, FilesSource, Layer, Source

STRICT = "strict"
FIRST = "first"
OVERRIDE = "override"
# Capability that lets a source create whole inventory entries (inventory only).
ANY = "*"

# Fields only catalog files (and environments/<env>.yaml) may set, at any depth of an entry. The
# whole `callers` section and `policy` are authorization data too.
AUTHORIZATION_FIELDS: dict[str, tuple[str, ...]] = {
    "targets": (
        "callers", "identity", "user_credential", "deploy.unit", "deploy.managed", "deploy.registry", "serving",
        "models", "google_apis", "gateway", "observability", "example",
    ),
    "teams": ("cost_centers", "external", "example"),
    "profiles": ("team", "repository_paths"),
}
AUTHORIZATION_FIELDS["inventory"] = AUTHORIZATION_FIELDS["targets"]
AUTHORIZATION_SECTIONS = ("callers", "policy")

# The most any non-files source may ever contribute: inventory plus attribution metadata. In
# `targets`, the paths are the inventory fields an adopted target may take from its source.
MAX_EXTERNAL_CAPABILITIES: dict[str, frozenset[str]] = {
    "inventory": frozenset({ANY}),
    "targets": frozenset({"deploy.endpoint", "runtime", "runtime_resource", "description", "system", "registry"}),
    "teams": frozenset({"projects"}),
    "profiles": frozenset({"description", "contacts", "oncall"}),
}
# Built-in allowlists per source type (code, not configuration).
SOURCE_CAPABILITIES: dict[str, dict[str, frozenset[str]]] = {
    "agent_registry": {
        "inventory": frozenset({ANY}),
        "targets": frozenset({"deploy.endpoint", "runtime", "runtime_resource", "description", "registry"}),
    },
    "project_labels": {
        "teams": frozenset({"projects"}),
        "inventory": frozenset({"owner_team"}),
    },
    "backstage": {
        "inventory": frozenset({ANY}),
        "targets": frozenset({"description", "system", "deploy.endpoint"}),
        "profiles": frozenset({"description", "contacts", "oncall"}),
    },
}

# Field strategies (default: strict). Paths are relative to the entry.
_ENTRY_STRATEGIES = {
    "kind": STRICT,
    "owner_team": STRICT,
    "deploy.endpoint": STRICT,
    "runtime": STRICT,
    "runtime_resource": STRICT,
    "registry.id": STRICT,
    "description": FIRST,
    "system": FIRST,
    "registry.skills": FIRST,
}
STRATEGIES: dict[str, dict[str, str]] = {
    "targets": _ENTRY_STRATEGIES,
    "inventory": _ENTRY_STRATEGIES,
    "profiles": {"description": FIRST, "contacts": FIRST, "oncall": FIRST},
    "teams": {"projects": STRICT},
}
# Inventory fields an adopted target must agree with even when its source may not fill them.
ADOPTION_STRICT = ("kind", "owner_team")

Capabilities = dict[str, frozenset[str]]


def _covered(path: str, allowed: frozenset[str] | tuple[str, ...]) -> bool:
    return any(path == a or path.startswith(a + ".") for a in allowed)


def value_hash(value: Any) -> str:
    """Short, stable hash of a value (for shadowed provenance records)."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def _canonical(value: Any) -> Any:
    """A deep copy of an external value with mapping keys sorted (adapter dict order never leaks)."""
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return copy.deepcopy(value)


def _is_files(source: Source) -> bool:
    return type(source) is FilesSource


def _check_within(caps: dict[str, Any], ceiling: dict[str, frozenset[str]], what: str, label: str) -> Capabilities:
    """``caps`` as frozensets, every path within ``ceiling`` and outside the authorization fields."""
    problems: list[str] = []
    out: Capabilities = {}
    for section in sorted(caps):
        paths = caps[section]
        paths = [paths] if isinstance(paths, str) else list(paths)
        top = ceiling.get(section, frozenset())
        for path in sorted(paths):
            if path == ANY:
                ok = ANY in top
            else:
                ok = (ANY in top or _covered(path, top)) and not _covered(path, AUTHORIZATION_FIELDS.get(section, ()))
            if not ok:
                problems.append(f"{label}: {what} '{section}.{path}' is beyond what this source type may contribute "
                                "(it can only be narrowed; authorization data comes from catalog files only)")
        out[section] = frozenset(paths)
    if problems:
        raise CatalogError(problems)
    return out


def source_capabilities(source: Source) -> Capabilities | None:
    """What ``source`` may contribute (``None``: unrestricted, the built-in ``files`` source only)."""
    if _is_files(source):
        return None
    label = f"catalog/sources.yaml: {source.name}"
    ceiling = SOURCE_CAPABILITIES.get(source.type_name, MAX_EXTERNAL_CAPABILITIES)
    declared = type(source).CAPABILITIES
    if declared is None:
        base = dict(SOURCE_CAPABILITIES.get(source.type_name, {}))
    else:
        base = _check_within(declared, ceiling, "capability", label)
    fields = (source.spec.get("options") or {}).get("fields")
    if fields is None:
        return base
    if not isinstance(fields, list) or not all(isinstance(f, str) and f for f in fields):
        raise CatalogError(f"{label}: options.fields must be a list of '<section>[.<field path>]' strings")
    narrowed: dict[str, set[str]] = {}
    for item in fields:
        section, _, path = item.partition(".")
        narrowed.setdefault(section, set()).add(path or ANY)
    return _check_within(narrowed, base, "options.fields entry", label)


@dataclass
class MergeResult:
    layer: Layer
    provenance: dict[str, dict[str, Any]] = field(default_factory=dict)
    adopted: dict[str, str] = field(default_factory=dict)  # target -> source name


class _Merger:
    def __init__(self) -> None:
        self.acc = Layer(source="resolved")
        self.provenance: dict[str, dict[str, Any]] = {}
        self.adopted: dict[str, str] = {}
        self.inventory_source: dict[str, str] = {}
        self.problems: list[str] = []

    # -- files ----------------------------------------------------------------------------

    def _fill(self, existing: Any, new: Any, path: str) -> Any:
        """Files over files: unset fields are filled, a different value is a conflict."""
        if isinstance(existing, dict) and isinstance(new, dict):
            out = dict(existing)
            for key in sorted(new):
                out[key] = self._fill(existing[key], new[key], f"{path}.{key}") if key in existing else new[key]
            return out
        if existing != new:
            self.problems.append(f"{path}: conflicting values {existing!r} and {new!r}")
        return existing

    def merge_files(self, layer: Layer) -> None:
        for section in SECTIONS:
            into = self.acc.section(section)
            entries = layer.section(section)
            for name in sorted(entries):
                origin = layer.origins.get((section, name), layer.source)
                key = f"{section}.{name}"
                if name in into:
                    where = f"{key} ({self.provenance[key]['origin']} vs {origin})"
                    into[name] = self._fill(into[name], entries[name], where)
                else:
                    into[name] = copy.deepcopy(entries[name])
                    self.provenance[key] = {"source": layer.source, "origin": origin}
        if layer.inventory:
            self.problems.append(f"{layer.source}: catalog files declare targets, not inventory")
        if self.acc.policy:
            self.acc.policy = self._fill(self.acc.policy, layer.policy, f"policy (source {layer.source})")
        else:
            self.acc.policy = copy.deepcopy(layer.policy)

    # -- external sources -----------------------------------------------------------------

    def _entry_problems(self, src: str, section: str, name: str, value: Any, caps: Capabilities) -> list[str]:
        """Fields of one external entry outside the source's capabilities, authorization ones first."""
        if not isinstance(value, dict):
            return [f"{src}: {section}.{name} must be a mapping"]
        allowed = caps.get(section, frozenset())
        denied = AUTHORIZATION_FIELDS.get(section, ())
        problems: list[str] = []

        def walk(node: dict[str, Any], prefix: str) -> None:
            for key in sorted(node):
                path = f"{prefix}{key}"
                sub = node[key]
                if _covered(path, denied):
                    problems.append(f"{src}: may not set {section}.{name}.{path} (authorization data comes from "
                                    "catalog files only)")
                elif ANY in allowed or _covered(path, allowed):
                    if isinstance(sub, dict) and any(d.startswith(path + ".") for d in denied):
                        walk(sub, path + ".")
                elif isinstance(sub, dict) and sub and any(a.startswith(path + ".") for a in allowed):
                    walk(sub, path + ".")
                else:
                    shown = ", ".join(sorted(allowed)) or "nothing"
                    problems.append(f"{src}: may not set {section}.{name}.{path} (source type capabilities for "
                                    f"{section}: {shown})")

        walk(value, "")
        return problems

    def _merge_fields(self, section: str, name: str, existing: dict[str, Any], new: dict[str, Any], src: str,
                      origin: str, only: frozenset[str] | None = None, prefix: str = "") -> None:
        """Merge ``new`` into ``existing`` by field strategy, recording field-level provenance.

        ``only``: the paths that may be filled (adoption); other strict fields must still agree.
        """
        strategies = STRATEGIES.get(section, {})
        entry_key = f"{section}.{name}"
        for key in sorted(new):
            path = f"{prefix}{key}"
            value = new[key]
            strategy = strategies.get(path)
            fillable = only is None or ANY in only or _covered(path, only)
            if strategy is None and isinstance(value, dict) and not isinstance(existing.get(key, {}), dict):
                strategy = STRICT  # a mapping against a scalar: report the conflict below
            if strategy is None and isinstance(value, dict):
                created = key not in existing
                if created:
                    if not (fillable or any(a.startswith(path + ".") for a in only or ())):
                        continue
                    existing[key] = {}
                self._merge_fields(section, name, existing[key], value, src, origin, only, path + ".")
                if created and not existing[key] and value:
                    del existing[key]
                continue
            strategy = strategy or STRICT
            field_key = f"{entry_key}.{path}"
            if key not in existing:
                if fillable:
                    existing[key] = _canonical(value)
                    self.provenance[field_key] = {"source": src, "origin": origin, "strategy": strategy}
                continue
            if existing[key] == value:
                continue
            holder = self.provenance.get(field_key) or self.provenance[entry_key]
            if strategy == FIRST:
                if fillable:
                    record = self.provenance.setdefault(
                        field_key, {"source": holder["source"], "origin": holder["origin"], "strategy": FIRST}
                    )
                    record.setdefault("shadowed", []).append(
                        {"source": src, "origin": origin, "value_hash": value_hash(value)}
                    )
                continue
            if fillable or path in ADOPTION_STRICT:
                self.problems.append(
                    f"{field_key}: {src} ({origin}) reports {value!r} but {holder['source']} ({holder['origin']}) "
                    f"has {existing[key]!r}; strict field, every source must agree (change the catalog file, "
                    "re-fetch the source, or override it in environments/<env>.yaml)"
                )

    def merge_external(self, source: Source, layer: Layer, caps: Capabilities) -> None:
        src = source.name
        if layer.policy:
            self.problems.append(f"{src}: may not set policy (authorization data comes from catalog files only)")
        for name in sorted(layer.callers):
            self.problems.append(f"{src}: may not set callers.{name} (callers and their identities come from "
                                 "catalog files only)")
        for name in sorted(layer.targets):
            value = layer.targets[name]
            found = self._entry_problems(src, "targets", name, value, {"targets": frozenset()})
            denied = [p for p in found if "authorization data" in p]
            self.problems += denied or [
                f"{src}: may not set targets.{name}: external sources report inventory, and a catalog file adopts "
                f"an entry only through a pin (deploy.registry: {{source: {src}, id: ...}}), never by name"
            ]
        for section in ("teams", "profiles", "inventory"):
            entries = layer.section(section)
            for name in sorted(entries):
                value = entries[name]
                origin = layer.origins.get((section, name), src)
                found = self._entry_problems(src, section, name, value, caps)
                if found:
                    self.problems += found
                    continue
                if section == "teams":
                    self._merge_team(src, name, value, origin)
                elif section == "profiles":
                    self._merge_profile(src, name, value, origin)
                else:
                    self._merge_inventory(src, name, value, origin, caps)

    def _merge_team(self, src: str, name: str, value: dict[str, Any], origin: str) -> None:
        if name not in self.acc.teams:
            self.problems.append(f"{src}: may not create team '{name}' (teams and their cost centres are declared "
                                 "in catalog files only)")
            return
        self._merge_fields("teams", name, self.acc.teams[name], value, src, origin)

    def _merge_profile(self, src: str, name: str, value: dict[str, Any], origin: str) -> None:
        if name not in self.acc.teams:
            self.problems.append(f"{src}: profiles.{name}: unknown team '{name}' (sources only reference teams "
                                 "declared in catalog files)")
            return
        if name not in self.acc.profiles:
            self.acc.profiles[name] = {"team": name}
            self.provenance[f"profiles.{name}"] = {"source": src, "origin": origin}
        self._merge_fields("profiles", name, self.acc.profiles[name], value, src, origin)

    def _merge_inventory(self, src: str, name: str, value: dict[str, Any], origin: str, caps: Capabilities) -> None:
        key = f"inventory.{name}"
        if name in self.acc.inventory:
            if ANY in caps.get("inventory", frozenset()):
                self.problems.append(f"{src}: inventory.{name}: also reported by {self.inventory_source[name]} "
                                     f"({self.provenance[key]['origin']}); two sources derive the same name, map one "
                                     "of them to another name in its options")
                return
            self._merge_fields("inventory", name, self.acc.inventory[name], value, src, origin)
            return
        if ANY not in caps.get("inventory", frozenset()):
            self.problems.append(f"{src}: may not create inventory.{name} (it may only add fields to entries an "
                                 "earlier source reported)")
            return
        self.acc.inventory[name] = _canonical(value)
        self.inventory_source[name] = src
        self.provenance[key] = {"source": src, "origin": origin}

    # -- adoption -------------------------------------------------------------------------

    def adopt(self, externals: dict[str, Capabilities], files_sources: set[str]) -> None:
        by_id: dict[tuple[str, str], str] = {}
        for name in sorted(self.acc.inventory):
            entry_id = (self.acc.inventory[name].get("registry") or {}).get("id")
            if entry_id is None:
                continue
            key = (self.inventory_source[name], entry_id)
            if key in by_id:
                self.problems.append(f"inventory.{name}: {key[0]} reports id '{entry_id}' twice (also as "
                                     f"inventory.{by_id[key]})")
                continue
            by_id[key] = name
        for target_name in sorted(self.acc.targets):
            target = self.acc.targets[target_name]
            pin = (target.get("deploy") or {}).get("registry")
            if not pin:
                continue
            label = f"targets.{target_name} ({self.provenance[f'targets.{target_name}']['origin']})"
            src, entry_id = pin["source"], pin["id"]
            if src not in externals:
                why = "a catalog files source" if src in files_sources else "not an enabled source"
                self.problems.append(f"{label}: deploy.registry pins source '{src}', which is {why} in "
                                     "catalog/sources.yaml")
                continue
            name = by_id.get((src, entry_id))
            if name is None:
                self.problems.append(f"{label}: deploy.registry pins id '{entry_id}' of source '{src}', which it "
                                     f"doesn't report: re-fetch {src} or remove the pin (an adopted target never "
                                     "resolves without its source entry)")
                continue
            entry = self.acc.inventory.pop(name)
            origin = self.provenance.pop(f"inventory.{name}")["origin"]
            for key in [k for k in self.provenance if k.startswith(f"inventory.{name}.")]:
                del self.provenance[key]
            del self.inventory_source[name]
            self.adopted[target_name] = src
            allowed = externals[src].get("targets", frozenset())
            self._merge_fields("targets", target_name, target, entry, src, origin, only=allowed)

    def check_names(self) -> None:
        for name in sorted(self.acc.inventory):
            src = self.inventory_source[name]
            if name in self.acc.targets:
                self.problems.append(f"{src}: inventory.{name} has the name of catalog target '{name}' "
                                     f"({self.provenance[f'targets.{name}']['origin']}); external entries never "
                                     "match by name: adopt it with deploy.registry, or map it to another name")
            elif name in self.acc.callers:
                self.problems.append(f"{src}: inventory.{name} has the name of catalog caller '{name}'")


def merge_layers(loaded: list[tuple[Source, Layer]]) -> MergeResult:
    """Merge the loaded layers (sources.yaml order), adopt pinned inventory and check the rules."""
    if not loaded:
        raise CatalogError("catalog/sources.yaml: no enabled source")
    first = loaded[0][0]
    if not _is_files(first):
        raise CatalogError(f"catalog/sources.yaml: the first enabled source must be of type files (got "
                           f"'{first.type_name}' for '{first.name}'): catalog files hold the authorization data")
    merger = _Merger()
    externals: dict[str, Capabilities] = {}
    files_sources: set[str] = set()
    names: set[str] = set()
    for source, layer in loaded:
        if source.name in names:
            raise CatalogError(f"catalog/sources.yaml: duplicate source name '{source.name}'")
        names.add(source.name)
        layer.source = source.name
        caps = source_capabilities(source)
        if caps is None:
            files_sources.add(source.name)
            merger.merge_files(layer)
        else:
            externals[source.name] = caps
            merger.merge_external(source, layer, caps)
    if not merger.problems:
        merger.adopt(externals, files_sources)
        merger.check_names()
    if merger.problems:
        raise CatalogError(merger.problems)
    return MergeResult(layer=merger.acc, provenance=merger.provenance, adopted=merger.adopted)


def _touched(patch: Any, parts: list[str]) -> bool:
    """Whether an environment override ``patch`` sets (or deletes) the value at ``parts``."""
    node = patch
    for part in parts:
        if not isinstance(node, dict):
            return True
        if part not in node:
            return False
        node = node[part]
    return True


def _lookup(doc: Any, parts: list[str]) -> Any:
    for part in parts:
        if not isinstance(doc, dict) or part not in doc:
            return None
        doc = doc[part]
    return doc


def apply_override_provenance(provenance: dict[str, dict[str, Any]], before: dict[str, Any], after: dict[str, Any],
                              overrides: dict[str, Any], env_label: str) -> dict[str, dict[str, Any]]:
    """Provenance after ``environments/<env>.yaml``: overridden external fields, added/removed entries."""
    out: dict[str, dict[str, Any]] = {}
    for key, record in provenance.items():
        section, name, *path = key.split(".", 2)
        if section in after and name not in after[section]:
            continue  # deleted by the override
        parts = [name, *path[0].split(".")] if path else [name]
        if path and section in overrides and _touched(overrides.get(section), parts):
            shadowed = [{"source": record["source"], "origin": record["origin"],
                         "value_hash": value_hash(_lookup(before.get(section), parts))}]
            record = {"source": env_label, "origin": env_label, "strategy": OVERRIDE,
                      "shadowed": shadowed + list(record.get("shadowed", []))}
        out[key] = record
    for section in LAYER_SECTIONS:
        for name in after.get(section) or {}:
            out.setdefault(f"{section}.{name}", {"source": env_label, "origin": env_label})
    return {key: out[key] for key in sorted(out)}
