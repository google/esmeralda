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

"""The Esmeralda catalog: resolve, compile and inspect (``esmeralda catalog resolve|show``).

The catalog says which targets exist (agents, MCP servers, models, legacy APIs), who owns them,
who may call them, through which gateway, and who pays. It is defined by
``contracts/catalog.schema.json`` and assembled from the sources listed in ``catalog/sources.yaml``:

1. **Resolve** (:func:`resolve`): merge the sources in order, apply ``environments/<env>.yaml``,
   apply the policy defaults, validate against the schema and the cross-references (unknown teams,
   callers or dimensions, duplicates fail).
2. **Compile** (:func:`compile_terraform`): the map the Terraform units consume today (``kind``,
   ``unit``, ``token``, ``probe``, ``timeout``, ``callers``), written to
   ``catalog/.resolved/<env>.json`` and decoded by ``infrastructure/live/<env>/env.yaml``. The
   runtime never reads the catalog sources directly. :func:`compile_iap_egress` derives, from the
   same callers, the Agent Gateway per-target ``roles/iap.egressor`` lists (``iap_egress`` in the
   same file, read by the ``iap-egress`` units), so one caller entry drives both Cloud Run /
   Agent Runtime IAM and the gateway. :func:`compile_external_targets` lists the Agent Registry
   entries of non-example ``managed: false`` targets (``external_targets``), whose caller lists are
   in ``iap_egress`` too. :func:`compile_attribution` derives the verified attribution
   block (``attribution``): the ``principal_teams`` rows (verified principal -> caller -> team),
   the shared identities, and the per-target allowlists of the in-process caller guard
   (``esmeralda.attribution``).
3. **Inspect** (:func:`show`): who can call each target, through which gateway, and who pays.
"""

from esmeralda.catalog.resolver import (
    CatalogError,
    Resolved,
    check_resolved,
    compile_attribution,
    compile_caller_allowlists,
    compile_external_targets,
    compile_iap_egress,
    compile_principal_teams,
    compile_terraform,
    render_hcl,
    render_resolved_file,
    resolve,
    resolved_path,
    show,
    write_resolved,
)
from esmeralda.catalog.sources import Layer, Source, register_source

__all__ = [
    "CatalogError",
    "Layer",
    "Resolved",
    "Source",
    "check_resolved",
    "compile_attribution",
    "compile_caller_allowlists",
    "compile_external_targets",
    "compile_iap_egress",
    "compile_principal_teams",
    "compile_terraform",
    "register_source",
    "render_hcl",
    "render_resolved_file",
    "resolve",
    "resolved_path",
    "show",
    "write_resolved",
]
