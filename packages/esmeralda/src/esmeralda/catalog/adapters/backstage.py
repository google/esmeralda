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

"""``backstage``: discovered inventory and team metadata from a Backstage software catalog.

The reference adapter for enterprise catalogs (a CMDB or API Hub adapter copies its shape).
Inventory and attribution metadata only, never authorization.

**Fetch** (``esmeralda catalog fetch --source <name>``, read-only, standard library only): the
Backstage Catalog API (``GET <base_url>/api/catalog/entities/by-query``, the filters of
``options.filters``, cursor pagination), or a JSON export the organization already produces
(``options.file``: a list of entities, or ``{"items": [...]}``). The token, when one is needed, is
a Backstage external-access token scoped to ``catalog.entity.read``, read at fetch time from
``options.token_env`` (the environment, or ``~/.config/esmeralda/env`` for ``ESMERALDA_*`` names)
or ``options.token_secret`` (a Secret Manager version). It is sent only to ``base_url`` (never on a
redirect) and never written to a snapshot, a log or an error message.

Mapping (the snapshot keeps only what follows, sorted by entity ref ``Kind:namespace/name``):

* ``Component`` with ``spec.type`` in ``options.component_types`` (default ``[agent, mcp-server,
  service]``): an inventory entry. ``kind`` from the ``esmeralda.dev/kind`` annotation, else
  ``agent`` -> ``a2a``, ``mcp-server`` -> ``mcp``, ``service`` -> ``http``; ``owner_team`` from
  ``spec.owner`` (a Group); ``description``; ``system`` from ``spec.system``; ``deploy.endpoint``
  from the ``esmeralda.dev/endpoint`` annotation, else from the first API it provides that has one.
  ``spec.lifecycle: experimental`` is skipped unless ``options.include_experimental``.
* ``API``: the endpoint of the Component that provides it. An API no fetched Component provides,
  with an ``esmeralda.dev/endpoint`` annotation, is an inventory entry of its own (``http``, or the
  ``esmeralda.dev/kind`` annotation, for example ``mcp``). API definitions are never copied.
* ``Resource`` with an ``esmeralda.dev/kind`` annotation: an inventory entry like a standalone API.
* ``System``: the ``system`` tag of the entities in it (descriptive, never enforced).
* ``Group``: a reference to a team the catalog files declare (``esmeralda.dev/team`` annotation,
  ``options.team_map``, else the group name). It fills the team profile ``description``,
  ``oncall`` (``esmeralda.dev/oncall``) and ``contacts`` (``esmeralda.dev/contacts``, comma
  separated; emails are dropped, snapshots never hold one) where ``team.yaml`` leaves them unset.
  Only the groups that own a fetched entity, or carry ``esmeralda.dev/team``, are kept.
* ``consumesApis`` / ``dependsOn``: **never callers**. ``esmeralda catalog diff`` lists them as
  ``suggested_grant`` hints for a human to declare (or not) in a catalog file.
* Identity annotations (service accounts, ...) are ignored: identities come from catalog files and
  unit outputs only.

**Resolve** (offline, from the snapshot): every entity is a discovered ``inventory`` entry, never
compiled. A catalog file adopts one only through an explicit pin, ``backstage: {source: <name>,
ref: Component:default/<name>}``, which may sit next to a ``deploy.registry`` pin (that is how an
Agent Registry entry and a Backstage entity for the same agent are joined). An adopted target takes
``description`` and ``system`` (``first``: catalog files win) and, when it isn't deployed by a unit,
``deploy.endpoint`` (``strict``: must agree); ``kind`` and ``owner_team`` must agree. Backstage
never creates teams and never extends a team's ``cost_centers``: a Group or owner with no catalog
team is a ``metadata_mismatch`` finding (``policy.attribution.label_mismatch`` decides whether it
fails the resolve).

Options (``sources.yaml``)::

    - name: backstage
      type: backstage
      enabled: false
      options:
        base_url: https://backstage.example.com     # https only (http only for localhost)
        # file: path/to/entities.json              # instead of base_url: a JSON export (repo-relative)
        token_env: ESMERALDA_BACKSTAGE_TOKEN        # default; or token_secret: projects/{project:<alias>}/secrets/<s>
        component_types: [agent, mcp-server, service]
        # filters: ["kind=component,spec.type=agent", "kind=api", "kind=group"]   # default: from component_types
        # include_experimental: false
        # names: {"Component:default/loan-helper": loan-helper-bs}   # catalog name (default: metadata.name)
        # team_map: {mortgage-squad: ai-coe}                         # group name or ref -> catalog team
        # placeholders: [domain, region]   # replace the entry point domain / region with {domain} / {region}
"""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from esmeralda.catalog.adapters import _live
from esmeralda.catalog.merge import entity_ref_key
from esmeralda.catalog.schema import CatalogError
from esmeralda.catalog.snapshot import Placeholders, SnapshotSource, SourceUnreachable
from esmeralda.catalog.sources import Layer, register_source

DEFAULT_COMPONENT_TYPES = ("agent", "mcp-server", "service")
TYPE_KINDS = {"agent": "a2a", "mcp-server": "mcp", "service": "http"}
KINDS = ("mcp", "a2a", "adk", "model", "http")
ANNOTATION_KIND = "esmeralda.dev/kind"
ANNOTATION_ENDPOINT = "esmeralda.dev/endpoint"
ANNOTATION_TEAM = "esmeralda.dev/team"
ANNOTATION_ONCALL = "esmeralda.dev/oncall"
ANNOTATION_CONTACTS = "esmeralda.dev/contacts"
DEFAULT_TOKEN_ENV = "ESMERALDA_BACKSTAGE_TOKEN"
QUERY_PATH = "/api/catalog/entities/by-query"
FIELDS = "kind,metadata,spec,relations"
PAGE_LIMIT = 200
MAX_PAGES = 500
MAX_BYTES = 64 * 1024 * 1024
TIMEOUT = 30
PROJECTS_UNIT = "layer-1-projects"
NETWORK_UNIT = "layer-2-networking"
FINDING = "metadata_mismatch"
SUGGESTION = "suggested_grant"
# Canonical spelling of the kinds in entity refs (Backstage compares kinds case-insensitively).
KIND_NAMES = {"component": "Component", "api": "API", "group": "Group", "system": "System",
              "resource": "Resource", "user": "User", "domain": "Domain", "location": "Location"}
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_ENTITY_PART = re.compile(r"^[A-Za-z0-9._-]+$")
_LOOPBACK = ("localhost", "127.0.0.1", "::1")
_PROJECT_PLACEHOLDER = re.compile(r"\{project:([a-z][a-z0-9_-]{0,62})\}")


def derive_name(raw: str) -> str:
    """A catalog-name candidate from an entity name (lower case, ``-`` separators)."""
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]+", "-", raw.strip().lower())).strip("-")


def parse_ref(ref: Any, default_kind: str, namespace: str = "default") -> tuple[str, str, str] | None:
    """``(kind, namespace, name)`` of an entity ref ``[kind:][namespace/]name`` (kind lower case)."""
    if not isinstance(ref, str) or not ref.strip():
        return None
    kind, sep, rest = ref.strip().partition(":")
    if not sep:
        kind, rest = default_kind, ref.strip()
    ns, sep, name = rest.partition("/")
    if not sep:
        ns, name = namespace, rest
    if not (_ENTITY_PART.match(kind) and _ENTITY_PART.match(ns) and _ENTITY_PART.match(name)):
        return None
    return kind.lower(), ns, name


def format_ref(kind: str, namespace: str, name: str) -> str:
    """The canonical ref ``Kind:namespace/name`` (``Component:default/loan-helper``)."""
    return f"{KIND_NAMES.get(kind.lower(), kind[:1].upper() + kind[1:].lower())}:{namespace}/{name}"


def entity_ref(entity: dict[str, Any]) -> str | None:
    metadata = entity.get("metadata") or {}
    parsed = parse_ref(f"{entity.get('kind')}:{metadata.get('namespace') or 'default'}/{metadata.get('name')}",
                       "component")
    return format_ref(*parsed) if parsed and entity.get("kind") and metadata.get("name") else None


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def _annotations(entity: dict[str, Any]) -> dict[str, str]:
    value = (entity.get("metadata") or {}).get("annotations") or {}
    return {k: str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def _relations(entity: dict[str, Any], relation: str) -> list[str]:
    out = []
    for item in entity.get("relations") or []:
        if isinstance(item, dict) and item.get("type") == relation:
            target = item.get("targetRef") or item.get("target")
            if isinstance(target, dict):
                target = f"{target.get('kind')}:{target.get('namespace') or 'default'}/{target.get('name')}"
            if isinstance(target, str):
                out.append(target)
    return out


def _refs(entity: dict[str, Any], spec_key: str, relation: str, default_kind: str) -> list[str]:
    """Canonical refs of a spec list (``providesApis``, ...) and its relation, de-duplicated, sorted."""
    raw = list((entity.get("spec") or {}).get(spec_key) or []) + _relations(entity, relation)
    found = {format_ref(*p) for p in (parse_ref(r, default_kind, _namespace(entity)) for r in raw) if p}
    return sorted(found, key=entity_ref_key)


def _namespace(entity: dict[str, Any]) -> str:
    return str((entity.get("metadata") or {}).get("namespace") or "default")


@register_source("backstage")
class BackstageSource(SnapshotSource):
    """Discovered inventory and team profile metadata from Backstage (see the module docstring)."""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._items: list[dict[str, Any]] = []  # from the snapshot: {id, name, inventory, consumes?}
        self._groups: list[dict[str, Any]] = []  # from the snapshot: {id, team, profile?}
        self._origin = ""
        self._placeholders = Placeholders()

    # -- options --------------------------------------------------------------------------

    def _error(self, message: str) -> CatalogError:
        return CatalogError(f"catalog/sources.yaml: {self.name}: {message}")

    def _strings(self, option: str, default: tuple[str, ...]) -> list[str]:
        value = self.options.get(option, list(default))
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise self._error(f"options.{option} must be a list of strings")
        return value

    def _mapping(self, option: str, pattern: re.Pattern[str] | None = None) -> dict[str, str]:
        value = self.options.get(option) or {}
        if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) and v
                                                  and (pattern is None or pattern.match(v))
                                                  for k, v in value.items()):
            shown = f" matching {pattern.pattern}" if pattern else ""
            raise self._error(f"options.{option} must map strings to names{shown}")
        return value

    @property
    def component_types(self) -> list[str]:
        return self._strings("component_types", DEFAULT_COMPONENT_TYPES)

    @property
    def include_experimental(self) -> bool:
        value = self.options.get("include_experimental", False)
        if not isinstance(value, bool):
            raise self._error("options.include_experimental must be true or false")
        return value

    @property
    def names(self) -> dict[str, str]:
        return {entity_ref_key(k): v for k, v in self._mapping("names", _NAME).items()}

    @property
    def team_map(self) -> dict[str, str]:
        return {entity_ref_key(k): v for k, v in self._mapping("team_map", _NAME).items()}

    @property
    def filters(self) -> list[str]:
        default = tuple(f"kind=component,spec.type={t}" for t in self.component_types) + (
            "kind=api", "kind=group", f"kind=resource,metadata.annotations.{ANNOTATION_KIND}")
        return self._strings("filters", default)

    @property
    def base_url(self) -> str:
        value = self.options.get("base_url")
        if not isinstance(value, str) or not value:
            raise self._error("set options.base_url (the Backstage URL) or options.file (a JSON export)")
        parsed = urllib.parse.urlsplit(value)
        loopback = parsed.scheme == "http" and parsed.hostname in _LOOPBACK
        if (parsed.scheme != "https" and not loopback) or not parsed.hostname or parsed.username \
                or parsed.password or parsed.query or parsed.fragment:
            raise self._error("options.base_url must be an https:// URL without credentials or query (http:// only "
                              "for localhost, for a local test instance)")
        return value.rstrip("/")

    # -- fetch (network or file, read-only) -----------------------------------------------

    def fetch(self) -> list[dict[str, Any]]:
        self._placeholders = self._literals()
        return self.entries_from_entities(self._entities())

    def placeholders(self) -> Placeholders:
        return self._placeholders

    def _literals(self) -> Placeholders:
        wanted = self._strings("placeholders", ())
        unknown = sorted(set(wanted) - {"domain", "region"})
        if unknown:
            raise self._error(f"options.placeholders: unknown {', '.join(unknown)} (known: domain, region)")
        env = self.env or ""
        domain = _live.unit_outputs(self.root, env, NETWORK_UNIT).get("entrypoint_domain") if "domain" in wanted \
            else None
        region = _live.env_region(self.root, env) if "region" in wanted else None
        return Placeholders(domain=domain or None, region=region or None)

    def _entities(self) -> list[dict[str, Any]]:
        file = self.options.get("file")
        if file is not None and self.options.get("base_url") is not None:
            raise self._error("set options.base_url or options.file, not both")
        if file is not None:
            return self._file_entities(file)
        return self._http_entities()

    def _file_entities(self, file: Any) -> list[dict[str, Any]]:
        if not isinstance(file, str) or not file:
            raise self._error("options.file must be a path (relative to the repository root)")
        path = Path(file) if Path(file).is_absolute() else self.root / file
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise SourceUnreachable(f"{self.name}: can't read the Backstage export {file}: {exc}") from None
        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise SourceUnreachable(f"{self.name}: {file} must hold a list of entities or {{\"items\": [...]}}")
        return items

    def _token(self) -> str | None:
        """The API token (``None``: anonymous, for a guest or local instance). Never logged."""
        env_name = self.options.get("token_env")
        secret = self.options.get("token_secret")
        if env_name is not None and secret is not None:
            raise self._error("set options.token_env or options.token_secret, not both")
        if secret is not None:
            if not isinstance(secret, str) or not secret:
                raise self._error("options.token_secret must be a Secret Manager resource name")
            resource = self._expand_projects(secret)
            value = _live.secret_text(self.root, resource, f"{self.name}: read the Backstage token secret")
            if not value:
                raise SourceUnreachable(f"{self.name}: the Backstage token secret is empty")
            return value
        explicit = env_name is not None
        env_name = env_name if explicit else DEFAULT_TOKEN_ENV
        if not isinstance(env_name, str) or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", env_name):
            raise self._error("options.token_env must be an environment variable name")
        from esmeralda.ops import envfile

        value = (os.environ.get(env_name) or "").strip() or (envfile.read().values.get(env_name) or "").strip()
        if not value and explicit:
            where = "the environment or ~/.config/esmeralda/env" if env_name.startswith(envfile.PREFIX) \
                else "the environment (only ESMERALDA_* names are read from ~/.config/esmeralda/env)"
            raise SourceUnreachable(f"{self.name}: no Backstage token: set {env_name} in {where}")
        return value or None

    def _expand_projects(self, text: str) -> str:
        aliases = sorted(set(_PROJECT_PLACEHOLDER.findall(text)))
        if not aliases:
            return text
        outputs = _live.unit_outputs(self.root, self.env or "", PROJECTS_UNIT)
        for alias in aliases:
            project = outputs.get(f"{alias}_project_id")
            if not project:
                raise SourceUnreachable(f"{self.name}: no {alias}_project_id output in {PROJECTS_UNIT} for "
                                        "options.token_secret")
            text = text.replace("{project:" + alias + "}", str(project))
        return text

    def _http_entities(self) -> list[dict[str, Any]]:
        base = self.base_url
        token = self._token()
        common = [("fields", FIELDS), ("limit", str(PAGE_LIMIT))]
        query = [("filter", f) for f in self.filters] + common
        items: list[dict[str, Any]] = []
        cursors: set[str] = set()
        for _ in range(MAX_PAGES):
            data = self._get_json(f"{base}{QUERY_PATH}?{urllib.parse.urlencode(query)}", base, token)
            page = data.get("items") if isinstance(data, dict) else None
            if not isinstance(page, list):
                raise SourceUnreachable(f"{self.name}: unexpected answer from the Backstage catalog API (no items)")
            items += page
            cursor = (data.get("pageInfo") or {}).get("nextCursor")
            if not cursor:
                return items
            if cursor in cursors:
                raise SourceUnreachable(f"{self.name}: the Backstage catalog API repeated a page cursor")
            cursors.add(cursor)
            query = [("cursor", cursor)] + common
        raise SourceUnreachable(f"{self.name}: more than {MAX_PAGES} pages from the Backstage catalog API: narrow "
                                "options.filters")

    def _get_json(self, url: str, base: str, token: str | None) -> Any:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        if token:
            request.add_unredirected_header("Authorization", f"Bearer {token}")  # never sent on a redirect
        context = ssl.create_default_context() if url.startswith("https://") else None
        handlers: list[Any] = [urllib.request.HTTPSHandler(context=context)] if context else []
        if urllib.parse.urlsplit(base).hostname in _LOOPBACK:
            handlers.append(urllib.request.ProxyHandler({}))  # a local instance is never behind a proxy
        opener = urllib.request.build_opener(*handlers)
        try:
            with opener.open(request, timeout=TIMEOUT) as response:
                body = response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            hint = " (check the token and its catalog.entity.read permission)" if code in (401, 403) else ""
            raise SourceUnreachable(f"{self.name}: the Backstage catalog API at {base} answered HTTP {code}{hint}") \
                from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", None) or type(exc).__name__
            message = f"{self.name}: can't reach the Backstage catalog API at {base}: {reason}"
            raise SourceUnreachable(message.replace(token, "<redacted>") if token else message) from None
        if len(body) > MAX_BYTES:
            raise SourceUnreachable(f"{self.name}: a Backstage catalog API page is larger than {MAX_BYTES} bytes")
        try:
            return json.loads(body)
        except ValueError:
            raise SourceUnreachable(f"{self.name}: the Backstage catalog API at {base} returned no JSON") from None

    # -- entities -> snapshot entries (pure) ----------------------------------------------

    def entries_from_entities(self, entities: list[Any]) -> list[dict[str, Any]]:
        """Snapshot entries from Backstage entities (no network): inventory entries and groups."""
        by_ref: dict[str, dict[str, Any]] = {}
        for entity in entities:
            if isinstance(entity, dict):
                ref = entity_ref(entity)
                if ref:
                    by_ref.setdefault(entity_ref_key(ref), entity)
        types = set(self.component_types)
        experimental = self.include_experimental
        components: dict[str, dict[str, Any]] = {}
        provided: dict[str, str] = {}  # API ref key -> ref of the component that provides it (imported or not)
        for key in sorted(by_ref):
            entity = by_ref[key]
            if str(entity.get("kind", "")).lower() != "component":
                continue
            for api in _refs(entity, "providesApis", "providesApi", "api"):
                provided.setdefault(entity_ref_key(api), entity_ref(entity) or "")
            spec = entity.get("spec") or {}
            if spec.get("type") not in types:
                continue
            if str(spec.get("lifecycle", "")).lower() == "experimental" and not experimental:
                continue
            components[key] = entity

        entries: list[dict[str, Any]] = []
        owners: set[str] = set()
        for key in sorted(components):
            entry, owner = self._component_entry(components[key], by_ref, provided)
            entries.append(entry)
            owners |= {owner} if owner else set()
        for key in sorted(by_ref):
            entity = by_ref[key]
            kind = str(entity.get("kind", "")).lower()
            annotations = _annotations(entity)
            standalone = kind == "api" and key not in provided and ANNOTATION_ENDPOINT in annotations
            if standalone or (kind == "resource" and ANNOTATION_KIND in annotations):
                spec = entity.get("spec") or {}
                if str(spec.get("lifecycle", "")).lower() == "experimental" and not experimental:
                    continue
                entry, owner = self._entry(entity, by_ref, "http", annotations.get(ANNOTATION_ENDPOINT))
                entries.append(entry)
                owners |= {owner} if owner else set()
        for key in sorted(by_ref):
            entity = by_ref[key]
            if str(entity.get("kind", "")).lower() == "group" and (key in owners
                                                                  or ANNOTATION_TEAM in _annotations(entity)):
                entries.append(self._group_entry(entity))
        teams: dict[str, str] = {}
        for entry in entries:
            if "team" in entry and entry["team"] in teams:
                raise CatalogError(f"{self.name}: groups {teams[entry['team']]} and {entry['id']} both map to team "
                                   f"'{entry['team']}' (set esmeralda.dev/team or options.team_map on one of them)")
            if "team" in entry:
                teams[entry["team"]] = entry["id"]
        return entries

    def _component_entry(self, entity: dict[str, Any], by_ref: dict[str, dict[str, Any]],
                         provided: dict[str, str]) -> tuple[dict, str | None]:
        annotations = _annotations(entity)
        endpoint = annotations.get(ANNOTATION_ENDPOINT)
        if not endpoint:
            for api_ref in _refs(entity, "providesApis", "providesApi", "api"):
                api = by_ref.get(entity_ref_key(api_ref))
                endpoint = _annotations(api).get(ANNOTATION_ENDPOINT) if api else None
                if endpoint and endpoint.startswith("https://"):
                    break
        kind = TYPE_KINDS.get(str((entity.get("spec") or {}).get("type")))
        entry, owner = self._entry(entity, by_ref, kind, endpoint)
        # What it uses, as the providing component's ref when Backstage knows it (else the API ref).
        uses = [provided.get(entity_ref_key(r), r) for r in _refs(entity, "consumesApis", "consumesApi", "api")]
        uses += [r for r in _refs(entity, "dependsOn", "dependsOn", "component") if r.split(":", 1)[0] == "Component"]
        uses = [r for r in uses if r and entity_ref_key(r) != entity_ref_key(entry["id"])]
        if uses:
            entry["consumes"] = sorted({entity_ref_key(r): r for r in uses}.values(), key=entity_ref_key)
        return entry, owner

    def _entry(self, entity: dict[str, Any], by_ref: dict[str, dict[str, Any]], default_kind: str | None,
               endpoint: str | None) -> tuple[dict[str, Any], str | None]:
        ref = entity_ref(entity) or ""
        metadata = entity.get("metadata") or {}
        spec = entity.get("spec") or {}
        annotations = _annotations(entity)
        kind = annotations.get(ANNOTATION_KIND) or default_kind
        if kind not in KINDS:
            raise CatalogError(f"{self.name}: {ref}: no catalog kind (set the {ANNOTATION_KIND} annotation to one "
                               f"of {', '.join(KINDS)})")
        name = self.names.get(entity_ref_key(ref)) or derive_name(str(metadata.get("name") or ""))
        if not _NAME.match(name):
            raise CatalogError(f"{self.name}: {ref} gives no valid catalog name ('{name}'); map it in options.names "
                               "(names never get an automatic suffix)")
        inventory: dict[str, Any] = {"kind": kind, "registry": {"id": ref}}
        description = _text(metadata.get("description")) or _text(metadata.get("title"))
        if description:
            inventory["description"] = description
        owner_key, team = self._owner(spec.get("owner"), entity, by_ref)
        if team:
            inventory["owner_team"] = team
        system = parse_ref(spec.get("system"), "system", _namespace(entity))
        if system and _NAME.match(system[2]):
            inventory["system"] = system[2]
        if isinstance(endpoint, str) and endpoint.startswith("https://"):
            inventory["deploy"] = {"endpoint": endpoint.strip()}
        return {"id": ref, "name": name, "inventory": inventory}, owner_key

    def _owner(self, owner: Any, entity: dict[str, Any], by_ref: dict[str, dict[str, Any]]) -> tuple[str | None, str]:
        """``(group ref key, catalog team candidate)`` of ``spec.owner`` (users own nothing here)."""
        parsed = parse_ref(owner, "group", _namespace(entity))
        if not parsed or parsed[0] != "group":
            return None, ""
        key = entity_ref_key(format_ref(*parsed))
        return key, self._team_of(key, parsed[2], by_ref.get(key))

    def _team_of(self, key: str, name: str, group: dict[str, Any] | None) -> str:
        annotated = _annotations(group).get(ANNOTATION_TEAM) if group else None
        team = annotated or self.team_map.get(key) or self.team_map.get(name.lower()) or derive_name(name)
        return team if _NAME.match(team) else ""

    def _group_entry(self, group: dict[str, Any]) -> dict[str, Any]:
        ref = entity_ref(group) or ""
        metadata = group.get("metadata") or {}
        annotations = _annotations(group)
        entry: dict[str, Any] = {"id": ref, "team": self._team_of(entity_ref_key(ref), str(metadata.get("name")),
                                                                    group)}
        if not entry["team"]:
            raise CatalogError(f"{self.name}: {ref} maps to no valid team name; set {ANNOTATION_TEAM} or "
                               "options.team_map")
        profile: dict[str, Any] = {}
        description = _text(metadata.get("description")) or _text(((group.get("spec") or {}).get("profile") or {})
                                                                   .get("displayName"))
        if description:
            profile["description"] = description
        if _text(annotations.get(ANNOTATION_ONCALL)):
            profile["oncall"] = _text(annotations[ANNOTATION_ONCALL])
        contacts = sorted({c.strip() for c in annotations.get(ANNOTATION_CONTACTS, "").split(",")
                           if c.strip() and "@" not in c})
        if contacts:
            profile["contacts"] = contacts
        if profile:
            entry["profile"] = profile
        return entry

    # -- snapshot -> layer ----------------------------------------------------------------

    def layer_from_entries(self, entries: list[dict[str, Any]], *, origin: str) -> Layer:
        """Keep the entries; contributions are made in :meth:`complete` (they depend on the teams)."""
        items, groups, problems = [], [], []
        for entry in entries:
            where = f"{origin}#{entry['id']}"
            if "team" in entry:
                profile = entry.get("profile", {})
                if set(entry) - {"id", "team", "profile"} or not _NAME.match(str(entry["team"])) \
                        or not isinstance(profile, dict) or set(profile) - {"description", "oncall", "contacts"}:
                    problems.append(f"{where}: a backstage group entry is {{id, team, profile?: {{description, oncall, "
                                    "contacts}}}}")
                    continue
                groups.append(entry)
            elif "name" in entry and isinstance(entry.get("inventory"), dict) and not set(entry) - {
                    "id", "name", "inventory", "consumes"}:
                if (entry["inventory"].get("registry") or {}).get("id") != entry["id"]:
                    problems.append(f"{where}: inventory.registry.id must be the entity ref")
                    continue
                items.append(entry)
            else:
                problems.append(f"{where}: a backstage entry is {{id, name, inventory, consumes?}} or a group entry")
        if problems:
            raise CatalogError(problems)
        self._items, self._groups, self._origin = items, groups, origin
        return Layer(source=self.name)

    def complete(self, layer: Layer, merged: Layer) -> Layer:
        """Inventory entries (``owner_team`` only for catalog teams) and the profiles of the groups
        that map to catalog teams."""
        for item in self._items:
            inventory = json.loads(json.dumps(item["inventory"]))
            if inventory.get("owner_team") not in merged.teams:
                inventory.pop("owner_team", None)
            layer.inventory[item["name"]] = inventory
            layer.origins[("inventory", item["name"])] = f"{self._origin}#{item['id']}"
        for group in self._groups:
            if group["team"] in merged.teams and group.get("profile"):
                layer.profiles[group["team"]] = json.loads(json.dumps(group["profile"]))
                layer.origins[("profiles", group["team"])] = f"{self._origin}#{group['id']}"
        return layer

    # -- findings and suggestions ---------------------------------------------------------

    def findings(self, document: dict[str, Any]) -> list[dict[str, str]]:
        teams = document.get("teams") or {}
        snapshot = self.snapshot_label() if self.env else self.name
        out = []
        for group in self._groups:
            if group["team"] not in teams:
                out.append({"kind": FINDING, "subject": group["id"],
                            "detail": f"maps to team '{group['team']}', which catalog files don't declare "
                                      f"({snapshot}); Backstage never creates teams: add the team, or set "
                                      "esmeralda.dev/team or options.team_map"})
        for item in self._items:
            owner = item["inventory"].get("owner_team")
            if owner and owner not in teams:
                out.append({"kind": FINDING, "subject": item["id"],
                            "detail": f"owner maps to team '{owner}', which catalog files don't declare; the entry "
                                      "is shown without an owner_team"})
        return out

    def suggestions(self, document: dict[str, Any]) -> list[dict[str, str]]:
        """``consumesApis`` / ``dependsOn`` as hints: never callers (a human declares the grant)."""
        names = {entity_ref_key(item["id"]): item["name"] for item in self._items}
        for target_name, target in sorted((document.get("targets") or {}).items()):
            pin = target.get("backstage") or {}
            if pin.get("source") == self.name:
                names[entity_ref_key(pin["ref"])] = target_name
        targets = document.get("targets") or {}
        out = []
        for item in self._items:
            consumer = names[entity_ref_key(item["id"])]
            for ref in item.get("consumes") or []:
                provider = names.get(entity_ref_key(ref))
                if provider and consumer in ((targets.get(provider) or {}).get("callers") or {}):
                    continue  # already granted in a catalog file
                to = f"'{provider}'" if provider else ref
                out.append({"kind": SUGGESTION, "subject": consumer,
                            "detail": f"Backstage says {item['id']} uses {ref}; Esmeralda never turns that into a "
                                      f"grant: if '{consumer}' should call {to}, declare it as a caller in that "
                                      "target's catalog file"})
        return out
