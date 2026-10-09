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

import subprocess

import pytest

from esmeralda import certs, cli

CERT_A = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"
CERT_B = "-----BEGIN CERTIFICATE-----\nBBBB\n-----END CERTIFICATE-----"


@pytest.fixture
def certifi_bundle(tmp_path):
    bundle = tmp_path / "cacert.pem"
    bundle.write_text("# public roots\n")
    return bundle


def test_parse_bundle_accepts_escaped_newlines():
    raw = (CERT_A + "\n" + CERT_B).replace("\n", "\\n")
    assert certs.parse_bundle(raw) == [CERT_A, CERT_B]


def test_unset_env_is_a_warning_not_an_error(certifi_bundle):
    assert certs.install_gateway_ca({}, certifi_path=str(certifi_bundle)) == 0
    assert certifi_bundle.read_text() == "# public roots\n"


def test_env_without_pem_fails(certifi_bundle):
    with pytest.raises(ValueError, match="no PEM certificate"):
        certs.install_gateway_ca({certs.ENV_VAR: "not a cert"}, certifi_path=str(certifi_bundle))


def test_installs_into_certifi_once(tmp_path, certifi_bundle):
    env = {certs.ENV_VAR: CERT_A + "\n" + CERT_B}
    missing_dir = tmp_path / "no-system-store"
    for _ in range(2):
        assert certs.install_gateway_ca(env, system_dir=missing_dir, certifi_path=str(certifi_bundle)) == 2
    content = certifi_bundle.read_text()
    assert content.count(CERT_A) == 1 and content.count(CERT_B) == 1


def test_installs_into_system_store(tmp_path, certifi_bundle, monkeypatch):
    system_dir = tmp_path / "ca-certificates"
    system_dir.mkdir()
    calls = []
    monkeypatch.setattr(certs.shutil, "which", lambda cmd: f"/usr/sbin/{cmd}")
    monkeypatch.setattr(certs.subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    certs.install_gateway_ca({certs.ENV_VAR: CERT_A}, system_dir=system_dir, certifi_path=str(certifi_bundle))

    assert (system_dir / "agw-1.crt").read_text() == CERT_A + "\n"
    assert calls == [["update-ca-certificates"]]


def test_system_store_failure_propagates(tmp_path, certifi_bundle, monkeypatch):
    system_dir = tmp_path / "ca-certificates"
    system_dir.mkdir()
    monkeypatch.setattr(certs.shutil, "which", lambda cmd: f"/usr/sbin/{cmd}")

    def fail(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(certs.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        certs.install_gateway_ca({certs.ENV_VAR: CERT_A}, system_dir=system_dir, certifi_path=str(certifi_bundle))


@pytest.mark.parametrize("argv", [[], ["bogus"], ["run"], ["run", "--"]])
def test_cli_usage_errors(argv, capsys):
    assert cli.main(argv) == 2
    assert "usage" in capsys.readouterr().err


def test_cli_run_installs_then_execs(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.certs, "install_gateway_ca", lambda: calls.append("install") or 1)
    monkeypatch.setattr(cli.os, "execvp", lambda file, args: calls.append(("exec", file, args)))

    cli.main(["run", "--", "adk", "api_server", "."])

    assert calls == ["install", ("exec", "adk", ["adk", "api_server", "."])]


def test_cli_run_stops_on_invalid_bundle(monkeypatch):
    def invalid():
        raise ValueError("bad bundle")

    monkeypatch.setattr(cli.certs, "install_gateway_ca", invalid)
    monkeypatch.setattr(cli.os, "execvp", lambda *a: pytest.fail("must not exec"))
    assert cli.main(["run", "true"]) == 1
