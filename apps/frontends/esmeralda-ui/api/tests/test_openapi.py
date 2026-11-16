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

"""The committed openapi.json matches the FastAPI app (the web's types are generated from it)."""

from __future__ import annotations

from esmeralda_ui import openapi


def test_committed_openapi_is_up_to_date():
    assert openapi.OPENAPI_PATH.is_file(), "run: python -m esmeralda_ui.openapi"
    assert openapi.OPENAPI_PATH.read_text() == openapi.render(), (
        "apps/frontends/esmeralda-ui/openapi.json is stale: run `python -m esmeralda_ui.openapi`, "
        "then `npm run gen:types` in apps/frontends/esmeralda-ui/web"
    )


def test_contract_covers_every_endpoint():
    paths = set(openapi.schema()["paths"])
    assert paths == {
        "/healthz",
        "/api/me",
        "/api/catalog",
        "/api/catalog/{target}",
        "/api/usage",
        "/api/audit",
        "/api/traces",
        "/api/traces/{trace_id}",
    }
