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

"""Catalog snapshots: what ``esmeralda catalog fetch`` read from an external source, normalized.

An external catalog source (Agent Registry, project labels, Backstage, a custom adapter) is read
in two steps, so that resolving the catalog never needs the network:

1. ``esmeralda catalog fetch --env <env> --source <name>`` calls the adapter's :meth:`SnapshotSource.fetch`
   (network, read-only on the source), normalizes the result and writes
   ``catalog/snapshots/<source>/<env>.json`` (``contracts/catalog-snapshot.schema.json``). The
   snapshot is committed and reviewed like any other catalog change.
2. ``esmeralda catalog resolve`` (and ``--check``, ``show``) reads only the snapshot
   (:meth:`SnapshotSource.load`). It never opens a socket, and its output is byte-reproducible.

Normalization makes a re-fetch of the same live state byte-identical: entries sorted by id, mapping
keys sorted, volatile fields (``etag``, ``createTime``, ...) dropped, no wall-clock value, and
environment-specific literals replaced with placeholders (``{org}``, ``{project:<alias>}``,
``{project_number:<alias>}``, ``{domain}``, ``{region}``; see :class:`Placeholders`). A committed
snapshot must hold no raw project number, organization id, email or IAM member, or token-like
string: :func:`snapshot_leaks` finds them, ``catalog fetch`` refuses to write them, and
``esmeralda guard`` rejects them.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from esmeralda.catalog.schema import CatalogError
from esmeralda.catalog.sources import Layer, Source

SNAPSHOT_SCHEMA = "esmeralda.catalog.snapshot"
SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_PATH = Path("contracts/catalog-snapshot.schema.json")
# Default snapshot location, relative to the catalog directory; options.snapshot may change it.
DEFAULT_SNAPSHOT = "snapshots/{source}/{env}.json"
# Fields dropped at fetch time, at any depth: they change without the entry changing.
VOLATILE_FIELDS = frozenset({
    "etag", "createTime", "updateTime", "create_time", "update_time", "fetched_at", "fetchedAt",
    "lastModified", "last_modified",
})
# Placeholders a snapshot may contain instead of environment-specific literals.
SIMPLE_PLACEHOLDERS = frozenset({"org", "domain", "region"})
ALIAS_PLACEHOLDERS = frozenset({"project", "project_number"})
_ALIAS = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


class SourceUnreachable(CatalogError):
    """The live system behind a source couldn't be read (``catalog fetch`` exits 2)."""


def fetch_command(env: str, source: str) -> str:
    return f"esmeralda catalog fetch --env {env} --source {source}"


# ------------------------------------------------------------------------------------------
# placeholders
# ------------------------------------------------------------------------------------------


def placeholder_problems(text: str) -> list[str]:
    """Unknown placeholders in ``text`` (known: ``{org}``, ``{domain}``, ``{region}``,
    ``{project:<alias>}``, ``{project_number:<alias>}``)."""
    problems = []
    for name in _PLACEHOLDER.findall(text):
        kind, sep, alias = name.partition(":")
        if (not sep and name in SIMPLE_PLACEHOLDERS) or (sep and kind in ALIAS_PLACEHOLDERS and _ALIAS.match(alias)):
            continue
        problems.append("{" + name + "}")
    return problems


@dataclass(frozen=True)
class Placeholders:
    """Environment-specific literals and the placeholder each one becomes in a snapshot.

    ``projects`` and ``project_numbers`` map an alias (a layer-1 output name such as ``gateway``)
    to the project id / number; units substitute the placeholders back from layer outputs at apply
    time, the way ``iap-egress`` does for ``{domain}``.
    """

    org: str | None = None
    domain: str | None = None
    region: str | None = None
    projects: Mapping[str, str] = field(default_factory=dict)
    project_numbers: Mapping[str, str] = field(default_factory=dict)

    def pairs(self) -> list[tuple[str, str]]:
        """``(literal, placeholder)`` pairs, longest literal first (ties by placeholder)."""
        found: list[tuple[str, str]] = []
        problems: list[str] = []
        for name in ("org", "domain", "region"):
            value = getattr(self, name)
            if value:
                found.append((str(value), "{" + name + "}"))
        for kind, mapping in (("project", self.projects), ("project_number", self.project_numbers)):
            for alias in sorted(mapping):
                if not _ALIAS.match(alias):
                    problems.append(f"placeholder alias '{alias}' must match {_ALIAS.pattern}")
                if mapping[alias]:
                    found.append((str(mapping[alias]), "{" + f"{kind}:{alias}" + "}"))
        for literal, placeholder in found:
            if placeholder in ("{org}",) or placeholder.startswith("{project_number:"):
                if not literal.isdigit():
                    problems.append(f"{placeholder}: '{literal}' must be digits")
        by_literal: dict[str, str] = {}
        for literal, placeholder in found:
            if literal in by_literal and by_literal[literal] != placeholder:
                problems.append(f"literal maps to two placeholders: {by_literal[literal]} and {placeholder}")
            by_literal.setdefault(literal, placeholder)
        if problems:
            raise CatalogError(problems)
        return sorted(by_literal.items(), key=lambda item: (-len(item[0]), item[1]))


def _boundaries(literal: str) -> tuple[str, str]:
    if literal.isdigit():
        return r"(?<![0-9])", r"(?![0-9])"
    if re.fullmatch(r"[a-z][a-z0-9-]*", literal) and "-" in literal:  # project-id-like: hyphens are part of it
        return r"(?<![A-Za-z0-9-])", r"(?![A-Za-z0-9-])"
    return r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"


def _replacer(pairs: list[tuple[str, str]]) -> tuple[re.Pattern[str], dict[str, str]] | None:
    if not pairs:
        return None
    parts, groups = [], {}
    for i, (literal, placeholder) in enumerate(pairs):
        before, after = _boundaries(literal)
        parts.append(f"(?P<g{i}>{before}{re.escape(literal)}{after})")
        groups[f"g{i}"] = placeholder
    return re.compile("|".join(parts)), groups


def _map_strings(value: Any, fn: Any) -> Any:
    if isinstance(value, dict):
        return {key: _map_strings(item, fn) for key, item in value.items()}
    if isinstance(value, list):
        return [_map_strings(item, fn) for item in value]
    if isinstance(value, str):
        return fn(value)
    return copy.deepcopy(value)


def normalize_placeholders(value: Any, placeholders: Placeholders) -> Any:
    """``value`` with every known environment-specific literal replaced by its placeholder.

    One pass, longest literal first, whole tokens only (``123`` never matches inside ``41234``), so
    the result doesn't depend on the order of the mappings.
    """
    compiled = _replacer(placeholders.pairs())
    if compiled is None:
        return copy.deepcopy(value)
    pattern, groups = compiled
    return _map_strings(value, lambda s: pattern.sub(lambda m: groups[m.lastgroup], s))


def substitute_placeholders(value: Any, placeholders: Placeholders) -> Any:
    """The reverse of :func:`normalize_placeholders` (placeholders with no value are kept)."""
    lookup = {placeholder: literal for literal, placeholder in placeholders.pairs()}
    return _map_strings(value, lambda s: _PLACEHOLDER.sub(lambda m: lookup.get(m.group(0), m.group(0)), s))


# ------------------------------------------------------------------------------------------
# leak check (also used by `esmeralda guard` on committed snapshots)
# ------------------------------------------------------------------------------------------

SNAPSHOT_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("raw 12-digit project number (use {project_number:<alias>})",
     re.compile(r"(?<![0-9A-Za-z])[0-9]{12}(?![0-9A-Za-z])")),
    ("organization or folder id (use {org})", re.compile(r"\b(?:organizations|folders)/[0-9]+")),
    ("organization id field (use {org})", re.compile(r"\"org(?:anization)?_?id\"\s*:\s*\"?[0-9]", re.IGNORECASE)),
    ("email address", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("IAM member or LDAP-like user", re.compile(r"\b(?:user|group|serviceAccount|deleted):[A-Za-z0-9._%+@-]+")),
    ("LDAP-like user field", re.compile(r"\"(?:ldap|username|user_name|owner_email)\"\s*:", re.IGNORECASE)),
    ("JWT-like token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("token-like string", re.compile(
        r"(?<![A-Za-z0-9_\-/.])(?=[A-Za-z0-9_\-]*[0-9])(?=[A-Za-z0-9_\-]*[A-Z])(?=[A-Za-z0-9_\-]*[a-z])"
        r"[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9_\-])")),
]


def _mask(text: str) -> str:
    return text if len(text) <= 8 else f"{text[:6]}…({len(text)} chars)"


def snapshot_leaks(text: str) -> list[tuple[str, str]]:
    """``(rule, masked match)`` for every environment literal or secret-like string in ``text``."""
    found = []
    for rule, pattern in SNAPSHOT_RULES:
        for match in pattern.finditer(text):
            found.append((rule, _mask(match.group(0))))
    return found


def is_snapshot_text(text: str) -> bool:
    """Whether a JSON file's content is a catalog snapshot (by its ``schema`` header)."""
    return re.search(r"\"schema\"\s*:\s*\"" + re.escape(SNAPSHOT_SCHEMA) + "\"", text) is not None


# ------------------------------------------------------------------------------------------
# document: build, render, load
# ------------------------------------------------------------------------------------------


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value) if key not in VOLATILE_FIELDS}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return copy.deepcopy(value)


def _volatile_paths(value: Any, path: str = "") -> list[str]:
    found = []
    if isinstance(value, dict):
        for key in sorted(value):
            here = f"{path}.{key}" if path else key
            if key in VOLATILE_FIELDS:
                found.append(here)
            found += _volatile_paths(value[key], here)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found += _volatile_paths(item, f"{path}[{i}]")
    return found


def normalize_entries(entries: list[dict[str, Any]], placeholders: Placeholders | None = None) -> list[dict[str, Any]]:
    """Entries as a snapshot stores them: placeholders applied, volatile fields dropped, keys and
    entries sorted. Duplicate ids or names fail (a collision is never resolved by order)."""
    problems: list[str] = []
    out = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            problems.append(f"entry {i}: needs a non-empty string id")
            continue
        value = normalize_placeholders(entry, placeholders) if placeholders else entry
        out.append(_canonical(value))
    out.sort(key=lambda e: e["id"])
    for kind in ("id", "name"):
        seen: set[str] = set()
        for entry in out:
            value = entry.get(kind)
            if value is None:
                continue
            if value in seen:
                problems.append(f"two entries have the {kind} '{value}' (map one of them to another name in the "
                                "source options; collisions are never resolved by order)")
            seen.add(value)
    if problems:
        raise CatalogError(problems)
    return out


def build_snapshot(*, source: str, type_name: str, env: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source": source,
        "type": type_name,
        "environment": env,
        "fetched_by": fetch_command(env, source),
        "entries": normalize_entries(entries),
    }


def render_snapshot(doc: dict[str, Any]) -> str:
    """The canonical bytes of a snapshot (sorted keys, two-space indent, trailing newline)."""
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@lru_cache(maxsize=8)
def _schema_cached(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def load_snapshot_schema(root: Path) -> dict[str, Any]:
    path = root / SNAPSHOT_SCHEMA_PATH
    if not path.is_file():
        raise CatalogError(f"{SNAPSHOT_SCHEMA_PATH} not found under {root}")
    return _schema_cached(str(path))


def validate_snapshot(root: Path, doc: Any, label: str, *, source: str | None = None, type_name: str | None = None,
                      env: str | None = None, text: str | None = None) -> list[str]:
    """Problems with a snapshot document (empty when valid): schema, header, order, placeholders,
    volatile fields, leaks and, when ``text`` is given, canonical formatting."""
    import jsonschema

    validator = jsonschema.Draft202012Validator(load_snapshot_schema(root))
    problems = []
    for err in sorted(validator.iter_errors(doc), key=lambda e: [str(p) for p in e.absolute_path]):
        where = "/".join(str(p) for p in err.absolute_path) or "(document)"
        problems.append(f"{label}: {where}: {err.message}")
    if problems:
        return problems
    for key, expected in (("source", source), ("type", type_name), ("environment", env)):
        if expected is not None and doc[key] != expected:
            problems.append(f"{label}: {key} is '{doc[key]}', expected '{expected}'")
    if doc["fetched_by"] != fetch_command(doc["environment"], doc["source"]):
        problems.append(f"{label}: fetched_by must be '{fetch_command(doc['environment'], doc['source'])}'")
    ids = [entry["id"] for entry in doc["entries"]]
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        problems.append(f"{label}: entries must be sorted by id, without duplicates")
    names = [entry["name"] for entry in doc["entries"] if "name" in entry]
    if len(set(names)) != len(names):
        problems.append(f"{label}: two entries have the same name")
    for path in _volatile_paths(doc["entries"], "entries"):
        problems.append(f"{label}: {path}: volatile field (snapshots carry no timestamps or etags)")
    rendered = render_snapshot(doc)
    strings: list[str] = []
    _map_strings(doc["entries"], strings.append)
    unknown = sorted({p for s in strings for p in placeholder_problems(s)})
    if unknown:
        problems.append(f"{label}: unknown placeholder(s) {', '.join(unknown)} (known: {{org}}, {{domain}}, "
                        "{region}, {project:<alias>}, {project_number:<alias>})")
    for rule, snippet in snapshot_leaks(rendered):
        problems.append(f"{label}: {rule} [{snippet}]")
    if text is not None and text != rendered:
        problems.append(f"{label}: not in canonical form (sorted keys, two-space indent): re-run "
                        f"`{fetch_command(doc['environment'], doc['source'])}`")
    return problems


def read_snapshot(root: Path, path: Path, label: str, **expected: Any) -> tuple[dict[str, Any], str]:
    """A snapshot file, validated, and the sha256 of its bytes."""
    try:
        text = path.read_text()
        doc = json.loads(text)
    except (OSError, ValueError) as exc:
        raise CatalogError(f"{label}: unreadable snapshot: {exc}") from exc
    problems = validate_snapshot(root, doc, label, text=text, **expected)
    if problems:
        raise CatalogError(problems)
    return doc, sha256_text(text)


# ------------------------------------------------------------------------------------------
# the source
# ------------------------------------------------------------------------------------------


class SnapshotSource(Source):
    """A source read from its snapshot; subclasses implement :meth:`fetch` (and usually
    :meth:`layer_from_entries` and :meth:`placeholders`).

    Options (``sources.yaml`` ``options``):

    * ``snapshot``: path of the snapshot, relative to ``catalog/`` (default
      ``snapshots/{source}/{env}.json``; ``{source}`` and ``{env}`` are substituted).
    * ``optional``: ``true`` turns a missing snapshot into a warning and an empty layer, as long
      as no catalog file adopts (pins) one of the source's entries.

    :meth:`load` never touches the network: it reads and validates the snapshot only. A missing
    snapshot is an error naming the ``catalog fetch`` command.
    """

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.snapshot_missing = False
        self.snapshot_sha256: str | None = None

    @property
    def options(self) -> dict[str, Any]:
        return self.spec.get("options") or {}

    @property
    def optional(self) -> bool:
        return self.options.get("optional") is True

    def snapshot_relpath(self, env: str | None = None) -> str:
        env = env or self.env
        if not env:
            raise CatalogError(f"catalog/sources.yaml: {self.name}: a snapshot source needs an environment")
        template = self.options.get("snapshot", DEFAULT_SNAPSHOT)
        if not isinstance(template, str) or not template:
            raise CatalogError(f"catalog/sources.yaml: {self.name}: options.snapshot must be a path string")
        rel = template.replace("{source}", self.name).replace("{env}", env)
        target = (self.catalog_dir / rel).resolve()
        if not target.is_relative_to(self.catalog_dir.resolve()) or not rel.endswith(".json"):
            raise CatalogError(f"catalog/sources.yaml: {self.name}: options.snapshot '{template}' must be a .json "
                               "path inside catalog/")
        return Path(rel).as_posix()

    def snapshot_path(self, env: str | None = None) -> Path:
        return self.catalog_dir / self.snapshot_relpath(env)

    def snapshot_label(self, env: str | None = None) -> str:
        """Repo-relative path of the snapshot (for messages and the resolved ``sources`` list)."""
        path = self.snapshot_path(env)
        return path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else str(path)

    def read(self, env: str | None = None) -> tuple[dict[str, Any], str] | None:
        """The validated snapshot and its sha256, or ``None`` when the file doesn't exist."""
        env = env or self.env
        path = self.snapshot_path(env)
        if not path.is_file():
            return None
        return read_snapshot(self.root, path, self.snapshot_label(env), source=self.name, type_name=self.type_name,
                             env=env)

    def load(self) -> Layer:
        found = self.read()
        if found is None:
            message = (f"{self.name}: snapshot {self.snapshot_label()} not found: run "
                       f"`{fetch_command(self.env or '<env>', self.name)}`")
            if not self.optional:
                raise CatalogError(message)
            self.snapshot_missing = True
            layer = Layer(source=self.name)
            layer.warnings.append(message + " (optional source: resolved without it)")
            return layer
        doc, self.snapshot_sha256 = found
        layer = self.layer_from_entries(doc["entries"], origin=self.snapshot_relpath())
        layer.source = self.name
        return layer

    # -- hooks for adapters ---------------------------------------------------------------

    def layer_from_entries(self, entries: list[dict[str, Any]], *, origin: str) -> Layer:
        """Turn snapshot entries into a layer. Default: each entry ``{id, name, inventory}`` is
        one inventory entry named ``name``, with origin ``<snapshot path>#<id>``."""
        layer = Layer(source=self.name)
        problems = []
        for entry in entries:
            if "name" not in entry or not isinstance(entry.get("inventory"), dict):
                problems.append(f"{origin}#{entry['id']}: the default snapshot entry shape is {{id, name, inventory}}")
                continue
            layer.inventory[entry["name"]] = copy.deepcopy(entry["inventory"])
            layer.origins[("inventory", entry["name"])] = f"{origin}#{entry['id']}"
        if problems:
            raise CatalogError(problems)
        return layer

    def placeholders(self) -> Placeholders:
        """Literals to replace with placeholders at fetch time (adapters read them from layer outputs)."""
        return Placeholders()

    def live_findings(self) -> list[dict[str, str]]:
        """Hook for ``catalog diff --live``, called after :meth:`fetch_snapshot`: what the live
        system is missing compared with the compiled catalog, as ``{"kind", "subject", "detail"}``
        rows (report only, never written). Default: none."""
        return []

    def fetch(self) -> list[dict[str, Any]]:
        """Read the live system (network, read-only) and return its entries, each with a stable ``id``.

        Raise :class:`SourceUnreachable` when the system can't be reached. The entries are
        normalized (:func:`normalize_entries`) before they are written; ``resolve`` never calls this.
        """
        raise NotImplementedError(f"catalog source type '{self.type_name}' ({self.name}) has no fetch adapter")

    def fetch_snapshot(self) -> dict[str, Any]:
        """Fetch, normalize and validate: the snapshot document ``catalog fetch`` would write."""
        try:
            raw = self.fetch()
        except NotImplementedError as exc:
            raise CatalogError(str(exc)) from exc
        entries = normalize_entries(raw, self.placeholders())
        doc = build_snapshot(source=self.name, type_name=self.type_name, env=self.env or "", entries=entries)
        label = f"{self.snapshot_label()} (fetched)"
        problems = validate_snapshot(self.root, doc, label, source=self.name, type_name=self.type_name, env=self.env)
        if problems:
            raise CatalogError(problems + [f"{self.name}: nothing written; map the literals to placeholders in the "
                                           "adapter (Placeholders) or drop the field"])
        self.layer_from_entries(doc["entries"], origin=self.snapshot_relpath())  # the entries must load
        return doc
