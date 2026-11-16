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

"""``esmeralda ui``: local-only, explicit demo/BigQuery choice, loopback bind, no default identity in cloud."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from esmeralda import cli

REPO_ROOT = Path(__file__).resolve().parents[3]
UI_SRC = REPO_ROOT / "apps" / "frontends" / "esmeralda-ui" / "api" / "src"


@pytest.fixture
def ui_package(monkeypatch):
    """The esmeralda-ui backend importable (it is a separate workspace package), with serve() captured."""
    if str(UI_SRC) not in sys.path:
        monkeypatch.syspath_prepend(str(UI_SRC))
    from esmeralda_ui import server

    calls = []
    monkeypatch.setattr(server, "serve", lambda settings, *, host, port: calls.append((settings, host, port)))
    return calls


def test_ui_requires_local(capsys):
    assert cli.main(["ui", "--repo", str(REPO_ROOT)]) == 2
    assert "--local" in capsys.readouterr().err


def test_demo_and_bigquery_are_exclusive():
    assert cli.main(["ui", "--local", "--demo", "--bigquery"]) == 2


def test_missing_backend_package_gives_a_hint(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "esmeralda_ui.server", None)  # import raises ImportError
    assert cli.main(["ui", "--local", "--repo", str(REPO_ROOT)]) == 2
    assert "make bootstrap" in capsys.readouterr().err


def test_local_demo_binds_loopback_with_labelled_identity(ui_package, monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert cli.main(["ui", "--local", "--demo", "--port", "8123", "--repo", str(REPO_ROOT)]) == 0
    [(settings, host, port)] = ui_package
    assert host == "127.0.0.1" and port == 8123
    assert settings.mode == "local" and settings.demo is True
    assert settings.data_source == "demo"
    assert settings.catalog_root == REPO_ROOT
    assert settings.bq_project is None


def test_local_bigquery_uses_explicit_project_and_default_dataset(ui_package, monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    argv = ["ui", "--local", "--bigquery", "--bq-project", "esm-dev-governance-abcd", "--repo", str(REPO_ROOT)]
    assert cli.main(argv) == 0
    [(settings, _, _)] = ui_package
    assert settings.data_source == "bigquery"
    assert settings.bq_dataset == "esmeralda_telemetry_logs_dev"
    assert settings.demo is False


def test_local_refuses_cloud_run(ui_package, monkeypatch, capsys):
    monkeypatch.setenv("K_SERVICE", "esmeralda-ui")
    assert cli.main(["ui", "--local", "--demo", "--repo", str(REPO_ROOT)]) == 2
    assert ui_package == []
