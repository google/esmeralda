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

"""``project_labels``: team attribution from the labels of the environment's projects.

Attribution metadata, never authorization. ``esmeralda catalog fetch --source <name>`` reads the
labels of the environment's projects (read-only ``gcloud projects describe``) and writes a snapshot
with one entry per project, keyed by its **alias** (the layer-output name, for example ``gateway``
for ``gateway_project_id``): project ids and numbers never reach the snapshot. Only the configured
label keys are kept.

At resolve time (offline, from the snapshot):

* each labelled project is added to ``teams.<team>.projects`` of the catalog team its ``team``
  label maps to, when the catalog files don't declare that team's projects themselves;
* an inventory entry another source reported, with no ``owner_team``, gets the team of the project
  in its ``runtime_resource`` (``{project:<alias>}``);
* disagreements are findings (``label_mismatch``): a label that maps to no catalog team, a
  ``cost-center`` label outside the team's declared ``cost_centers``, a project the catalog files
  give to another team, a declared ``owner_team`` that differs from its project's label.
  ``policy.attribution.label_mismatch`` makes them fail the resolve (``fail``, the default) or
  warnings (``warn``, only in ``flag_environments``); ``catalog diff`` always lists them.

Labels never create teams, never extend a team's ``cost_centers`` (that list bounds what callers
may declare, so widening it is an authorization change) and never override catalog files.

Options (``sources.yaml``)::

    - name: project-labels
      type: project_labels
      enabled: false
      options:
        team_label: team                 # label key holding the team (default: team)
        cost_center_label: cost-center   # label key holding the cost centre (default: cost-center)
        team_map: {appdev-tools: platform}   # label value -> catalog team (default: same name)
        cost_center_map: {}                  # label value -> catalog cost centre (default: same)
        projects:                            # default: every <alias>_project_id output of layer-1-projects
          - {alias: gateway, output: "layer-1-projects:gateway_project_id"}
"""

from __future__ import annotations

import re
from typing import Any

from esmeralda.catalog.adapters import _live
from esmeralda.catalog.schema import CatalogError
from esmeralda.catalog.snapshot import Placeholders, SnapshotSource, SourceUnreachable
from esmeralda.catalog.sources import Layer, register_source

DEFAULT_UNIT = "layer-1-projects"
PROJECT_ID_SUFFIX = "_project_id"
_ALIAS = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_LABEL_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_PROJECT_REF = re.compile(r"\{project(?:_number)?:([a-z][a-z0-9_-]{0,62})\}")
_OUTPUT_REF = re.compile(r"^(?P<unit>[a-z0-9_./-]+):(?P<output>[a-z0-9_]+)$")
KIND = "label_mismatch"


def project_alias(resource: Any) -> str | None:
    """The project alias in a runtime resource name (``{project:<alias>}``), if any."""
    match = _PROJECT_REF.search(resource) if isinstance(resource, str) else None
    return match.group(1) if match else None


@register_source("project_labels")
class ProjectLabelsSource(SnapshotSource):
    """Team attribution from project labels (see the module docstring)."""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._labels: dict[str, dict[str, str]] = {}  # alias -> kept labels (from the snapshot)
        self._origin = ""
        self._ids: dict[str, str] = {}  # fetch only: alias -> project id / number (placeholders)
        self._numbers: dict[str, str] = {}

    # -- options --------------------------------------------------------------------------

    def _label_key(self, option: str, default: str) -> str:
        value = self.options.get(option, default)
        if not isinstance(value, str) or not _LABEL_KEY.match(value):
            raise CatalogError(f"catalog/sources.yaml: {self.name}: options.{option} must be a label key "
                               f"matching {_LABEL_KEY.pattern}")
        return value

    @property
    def team_label(self) -> str:
        return self._label_key("team_label", "team")

    @property
    def cost_center_label(self) -> str:
        return self._label_key("cost_center_label", "cost-center")

    def _map(self, option: str) -> dict[str, str]:
        value = self.options.get(option) or {}
        if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) and v
                                                  for k, v in value.items()):
            raise CatalogError(f"catalog/sources.yaml: {self.name}: options.{option} must map label values to "
                               "catalog names")
        return value

    def team_of(self, alias: str) -> str | None:
        """The catalog team a project's ``team`` label maps to (``None``: no label)."""
        value = self._labels.get(alias, {}).get(self.team_label)
        return None if value is None else self._map("team_map").get(value, value)

    def cost_center_of(self, alias: str) -> str | None:
        value = self._labels.get(alias, {}).get(self.cost_center_label)
        return None if value is None else self._map("cost_center_map").get(value, value)

    def project_refs(self) -> dict[str, tuple[str, str]]:
        """``alias -> (unit, output)`` of the projects to read (``options.projects``)."""
        projects = self.options.get("projects")
        if projects is None:
            return {}
        problems, out = [], {}
        if not isinstance(projects, list):
            projects = [projects]
        for i, item in enumerate(projects):
            where = f"catalog/sources.yaml: {self.name}: options.projects[{i}]"
            if not isinstance(item, dict) or set(item) != {"alias", "output"}:
                problems.append(f"{where} must be {{alias, output: <unit>:<output>}}")
                continue
            alias, ref = item["alias"], item["output"]
            match = _OUTPUT_REF.match(ref) if isinstance(ref, str) else None
            if not isinstance(alias, str) or not _ALIAS.match(alias) or alias in out:
                problems.append(f"{where}: alias must match {_ALIAS.pattern} and be unique")
            elif not match:
                problems.append(f"{where}: output must be '<unit>:<output>'")
            else:
                out[alias] = (match["unit"], match["output"])
        if problems:
            raise CatalogError(problems)
        return out

    # -- snapshot -> layer ----------------------------------------------------------------

    def layer_from_entries(self, entries: list[dict[str, Any]], *, origin: str) -> Layer:
        """Keep the labels of each project; contributions are made in :meth:`complete`."""
        keys = {self.team_label, self.cost_center_label}
        labels: dict[str, dict[str, str]] = {}
        problems = []
        for entry in entries:
            where = f"{origin}#{entry['id']}"
            value = entry.get("labels", {})
            if set(entry) - {"id", "labels"} or not _ALIAS.match(entry["id"]):
                problems.append(f"{where}: a project_labels entry is {{id: <project alias>, labels: {{...}}}}")
            elif not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                        for k, v in value.items()):
                problems.append(f"{where}: labels must map label keys to strings")
            else:
                labels[entry["id"]] = {k: v for k, v in value.items() if k in keys}
        if problems:
            raise CatalogError(problems)
        self._labels, self._origin = labels, origin
        return Layer(source=self.name)

    def complete(self, layer: Layer, merged: Layer) -> Layer:
        """``teams.<team>.projects`` for catalog teams whose projects the files don't declare, and
        ``owner_team`` of inventory entries without one (by the project of their runtime resource)."""
        declared = {alias for team in merged.teams.values() for alias in team.get("projects") or []}
        by_team: dict[str, list[str]] = {}
        for alias in sorted(self._labels):
            team = self.team_of(alias)
            if team in merged.teams and "projects" not in merged.teams[team] and alias not in declared:
                by_team.setdefault(team, []).append(alias)
        for team, aliases in sorted(by_team.items()):
            layer.teams[team] = {"projects": aliases}
            layer.origins[("teams", team)] = f"{self._origin}#{','.join(aliases)}"
        for name in sorted(merged.inventory):
            entry = merged.inventory[name]
            alias = project_alias(entry.get("runtime_resource"))
            team = self.team_of(alias) if alias else None
            if "owner_team" not in entry and team in merged.teams:
                layer.inventory[name] = {"owner_team": team}
                layer.origins[("inventory", name)] = f"{self._origin}#{alias}"
        return layer

    # -- findings -------------------------------------------------------------------------

    def findings(self, document: dict[str, Any]) -> list[dict[str, str]]:
        teams = document.get("teams") or {}
        declared: dict[str, list[str]] = {}  # alias -> teams whose projects list it
        for team in sorted(teams):
            for alias in teams[team].get("projects") or []:
                declared.setdefault(alias, []).append(team)
        out: list[dict[str, str]] = []

        def add(subject: str, detail: str) -> None:
            out.append({"kind": KIND, "subject": subject, "detail": detail})

        snapshot = self.snapshot_label() if self.env else self.name
        for alias in sorted(self._labels):
            subject = f"project {alias}"
            team = self.team_of(alias)
            if team is None:
                add(subject, f"has no '{self.team_label}' label ({snapshot})")
                continue
            label = self._labels[alias][self.team_label]
            if team not in teams:
                add(subject, f"label {self.team_label}={label} maps to team '{team}', which catalog files don't "
                             "declare (labels never create teams: relabel the project or add a team_map entry)")
                continue
            owners = declared.get(alias, [])
            if owners and team not in owners:
                add(subject, f"teams.{', teams.'.join(owners)}.projects lists it, but its label "
                             f"{self.team_label}={label} says '{team}'")
            elif not owners:
                add(subject, f"label {self.team_label}={label} says '{team}', but teams.{team}.projects (catalog "
                             "files) doesn't list it")
            cost_center = self.cost_center_of(alias)
            allowed = teams[team].get("cost_centers") or []
            if cost_center is not None and cost_center not in allowed:
                add(subject, f"label {self.cost_center_label}={self._labels[alias][self.cost_center_label]} is not a "
                             f"cost centre of team '{team}' ({', '.join(allowed)}); labels never extend cost_centers")
        for alias in sorted(set(declared) - set(self._labels)):
            add(f"project {alias}", f"teams.{', teams.'.join(declared[alias])}.projects lists it, but {snapshot} "
                                    "doesn't report it")
        for section in ("targets", "inventory"):
            entries = document.get(section) or {}
            for name in sorted(entries):
                entry = entries[name]
                alias = project_alias(entry.get("runtime_resource"))
                team = self.team_of(alias) if alias in self._labels else None
                owner = entry.get("owner_team")
                if team in teams and owner is not None and owner != team:
                    add(f"{section}.{name}", f"owner_team is '{owner}', but its project '{alias}' has the label "
                                             f"{self.team_label}={self._labels[alias][self.team_label]} ('{team}')")
        return out

    # -- fetch (network, read-only) -------------------------------------------------------

    def _project_ids(self) -> dict[str, str]:
        """``alias -> project id`` from the configured unit outputs (default: layer-1 ``*_project_id``)."""
        env = self.env or ""
        refs = self.project_refs()
        ids: dict[str, str] = {}
        if refs:
            cache: dict[str, dict[str, Any]] = {}
            for alias in sorted(refs):
                unit, output = refs[alias]
                outputs = cache.setdefault(unit, _live.unit_outputs(self.root, env, unit))
                if not outputs.get(output):
                    raise SourceUnreachable(f"{self.name}: no output '{output}' in {unit} ({env}): deploy it first")
                ids[alias] = str(outputs[output])
        else:
            outputs = _live.unit_outputs(self.root, env, DEFAULT_UNIT)
            for name in sorted(outputs):
                if name.endswith(PROJECT_ID_SUFFIX) and isinstance(outputs[name], str) and outputs[name]:
                    ids[name[: -len(PROJECT_ID_SUFFIX)]] = outputs[name]
            if not ids:
                raise SourceUnreachable(f"{self.name}: no *{PROJECT_ID_SUFFIX} output in {DEFAULT_UNIT} ({env}): "
                                        "deploy layer 1 first, or list the projects in options.projects")
        bad = sorted(alias for alias in ids if not _ALIAS.match(alias))
        if bad:
            raise CatalogError(f"{self.name}: project alias(es) {', '.join(bad)} must match {_ALIAS.pattern}")
        return ids

    def fetch(self) -> list[dict[str, Any]]:
        keys = (self.team_label, self.cost_center_label)
        self._ids = self._project_ids()
        self._numbers = {}
        entries = []
        for alias in sorted(self._ids):
            project = _live.gcloud_json(self.root, ["projects", "describe", self._ids[alias]],
                                        f"{self.name}: projects describe ({alias})") or {}
            number = str(project.get("projectNumber") or "")
            if number.isdigit():
                self._numbers[alias] = number
            labels = project.get("labels") or {}
            entries.append({"id": alias, "labels": {k: str(labels[k]) for k in keys if k in labels}})
        return entries

    def placeholders(self) -> Placeholders:
        # One placeholder per literal: two aliases may name the same project (a shared or BYO project).
        projects: dict[str, str] = {}
        numbers: dict[str, str] = {}
        for alias in sorted(self._ids):
            if self._ids[alias] not in projects.values():
                projects[alias] = self._ids[alias]
                if alias in self._numbers:
                    numbers[alias] = self._numbers[alias]
        return Placeholders(projects=projects, project_numbers=numbers)
