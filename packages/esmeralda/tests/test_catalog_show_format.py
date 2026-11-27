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

"""Golden and unit tests for ``esmeralda catalog show`` human formatting."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
from pathlib import Path

import pytest

from esmeralda import catalog, cli
from esmeralda.catalog import format as catalog_format

REPO = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "catalog_show"
GOLDEN_CATALOG = Path(__file__).resolve().parent / "fixtures" / "catalog_golden"

GOLDEN_CASES = (
    ("dev.txt", ("catalog", "show", "--env", "dev")),
    ("prd.txt", ("catalog", "show", "--env", "prd")),
    ("dev.tree.txt", ("catalog", "show", "--env", "dev", "--format", "tree")),
)


def _run_cli(*args: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main([*args, "--repo", str(REPO)])
    return code, out.getvalue()


@pytest.mark.parametrize(("fixture_name", "argv"), GOLDEN_CASES)
def test_human_output_matches_golden_files(fixture_name: str, argv: tuple[str, ...], monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    code, output = _run_cli(*argv)
    assert code == 0
    expected = (FIXTURES / fixture_name).read_text()
    assert output == expected, (
        f"{fixture_name} is stale; regenerate with "
        "uv run --frozen python packages/esmeralda/tests/test_catalog_show_format.py --update"
    )


@pytest.mark.parametrize("env", ["dev", "prd"])
@pytest.mark.parametrize("fmt", ["table", "tree"])
def test_sample_catalog_fits_within_120_columns(env: str, fmt: str, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    code, output = _run_cli("catalog", "show", "--env", env, "--format", fmt)
    assert code == 0
    assert "\033[" not in output
    too_wide = [(idx, len(line), line) for idx, line in enumerate(output.splitlines(), 1) if len(line) > 120]
    assert not too_wide, f"lines exceed 120 columns ({env}, {fmt}): {too_wide}"


def test_json_output_is_unchanged_by_format_and_legend_flags(capsys):
    assert cli.main(["catalog", "show", "--env", "dev", "--json", "--repo", str(REPO)]) == 0
    baseline = capsys.readouterr().out
    assert cli.main(["catalog", "show", "--env", "dev", "--format", "tree", "--no-legend", "--json",
                     "--repo", str(REPO)]) == 0
    assert capsys.readouterr().out == baseline
    parsed = json.loads(baseline)
    assert parsed["env"] == "dev" and len(parsed["targets"]) == 9


def test_route_mismatch_is_highlighted_and_matching_routes_print_once(tmp_path: Path, capsys):
    repo = tmp_path / "repo"
    shutil.copytree(REPO / "catalog", repo / "catalog")
    shutil.copytree(REPO / "contracts", repo / "contracts")
    (repo / "infrastructure/live/dev").mkdir(parents=True)
    (repo / "infrastructure/live/root.hcl").write_text("")
    (repo / "infrastructure/live/dev/env.yaml").write_text(
        'locals {\n  gateways = {\n    enabled = ["envoy"]\n    default = "direct"\n'
        '    host_rules = { "legacy-dms" = "envoy" }\n  }\n}\n'
    )

    assert cli.main(["catalog", "show", "--env", "dev", "--target", "legacy-dms", "--no-legend",
                     "--repo", str(repo)]) == 0
    drift_out = capsys.readouterr().out
    assert "route: gateway=direct != ingress=envoy" in drift_out
    assert "1 route mismatch" in drift_out

    assert cli.main(["catalog", "show", "--env", "dev", "--target", "corporate-email", "--no-legend",
                     "--repo", str(repo)]) == 0
    clean_out = capsys.readouterr().out
    assert "route: direct" in clean_out
    assert "gateway=" not in clean_out and "ingress=" not in clean_out


def test_badges_for_enforced_declared_only_example_and_adopted(tmp_path: Path):
    repo = tmp_path / "repo"
    shutil.copytree(GOLDEN_CATALOG / "catalog", repo / "catalog")
    (repo / "contracts").mkdir(parents=True)
    for name in ("catalog.schema.json", "catalog-snapshot.schema.json"):
        shutil.copy(REPO / "contracts" / name, repo / "contracts")
    (repo / "infrastructure/live/dev").mkdir(parents=True)
    (repo / "infrastructure/live/root.hcl").write_text("")

    # Add a group caller (not enforced) on an external target and a compiled target.
    callers_path = repo / "catalog/teams/platform/callers.yaml"
    callers_path.write_text(
        callers_path.read_text()
        + "  ops-group:\n    team: platform\n    identity: { type: group, id: ops@example.com }\n"
    )
    ext_path = repo / "catalog/external/legacy/pricing-api.yaml"
    ext_path.write_text(
        ext_path.read_text().replace(
            "  ai-coe-mortgage-specialist: {}\n",
            "  ai-coe-mortgage-specialist: {}\n  ops-group: {}\n",
        )
    )
    resolved = catalog.resolve(repo, "dev")
    data = catalog.show(resolved, ingress={"default": "direct", "host_rules": {}})
    rendered = catalog_format.format_show(data, color=False)

    assert "[adopted from backstage]" in rendered
    assert "[adopted from agent-registry, backstage]" in rendered
    assert "registry: agents (adopted engine URLs, per-target)" in rendered
    assert "[enforced]" in rendered
    assert "[declared only]" in rendered
    assert all(len(line) <= 120 for line in rendered.splitlines())


def test_color_respects_tty_and_no_color_env(monkeypatch, capsys):
    resolved = catalog.resolve(REPO, "prd")
    data = catalog.show(resolved, target="example-core-banking")

    # Non-TTY stdout never emits ANSI sequences even when NO_COLOR is unset.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    plain = catalog_format.format_show(data)
    assert "\033[" not in plain
    assert "[example]" in plain and "[not compiled]" in plain and "[declared only]" in plain

    # TTY stdout emits ANSI sequences when NO_COLOR is unset or empty.
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    colored = catalog_format.format_show(data)
    assert "\033[" in colored
    monkeypatch.setenv("NO_COLOR", "")
    assert "\033[" in catalog_format.format_show(data)

    # Non-empty NO_COLOR suppresses ANSI sequences even on a TTY.
    monkeypatch.setenv("NO_COLOR", "1")
    no_color = catalog_format.format_show(data)
    assert "\033[" not in no_color
    assert no_color == plain

    # CLI run via capsys (non-TTY) stays clean.
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert cli.main(["catalog", "show", "--env", "dev", "--target", "legacy-dms", "--repo", str(REPO)]) == 0
    assert "\033[" not in capsys.readouterr().out


def test_legend_flag_and_newcomer_terms(capsys):
    assert cli.main(["catalog", "show", "--env", "dev", "--repo", str(REPO)]) == 0
    with_legend = capsys.readouterr().out
    for term in ("Summary:", "Legend:", "via", "workload (agent)", "unmapped_caller", "declared_mismatch",
                 "[enforced]", "[declared only]", "[example]", "[not compiled]"):
        assert term in with_legend

    assert cli.main(["catalog", "show", "--env", "dev", "--no-legend", "--repo", str(REPO)]) == 0
    no_legend = capsys.readouterr().out
    assert "Summary:" in no_legend
    assert "Legend:" not in no_legend

    assert cli.main(["catalog", "show", "--env", "dev", "--legend", "--repo", str(REPO)]) == 0
    assert capsys.readouterr().out == with_legend


def test_extra_caller_fields_and_empty_targets_fit_120_columns():
    data = {
        "env": "dev",
        "policy": {"unmapped_caller": "deny", "declared_mismatch": "reject"},
        "targets": [
            {
                "target": "billing-api",
                "kind": "http",
                "owner_team": "platform",
                "example": False,
                "deploy": "https://billing.example.com",
                "compiled": False,
                "registry": "endpoints (per-target caller list)",
                "gateway": "apigee",
                "ingress_backend": None,
                "callers": [
                    {
                        "caller": "very-long-partner-application-name",
                        "identity": "apigee_app",
                        "team": "example-partner-organization",
                        "gateway": "apigee",
                        "pays": {
                            "team": "example-partner-organization",
                            "cost_centers": ["cc-partner-br", "cc-partner-mx", "cc-partner-ar"],
                        },
                        "enforced": False,
                        "product": "tier-enterprise",
                        "tools": ["list_invoices", "get_balance"],
                        "quota": {"tokens_per_minute": 5000, "requests_per_minute": 60},
                    }
                ],
            }
        ],
    }
    for fmt in ("table", "tree"):
        out = catalog_format.format_show(data, fmt=fmt, color=False)
        assert "tools=list_invoices" in out and "get_balance" in out
        assert "quota=5000tpm,60rpm" in out
        assert all(len(line) <= 120 for line in out.splitlines()), (fmt, out)

    empty = catalog_format.format_show({"env": "dev", "policy": {}, "targets": []}, color=False)
    assert "(no matching targets)" in empty
    assert "Summary: 0 targets · 0 grants" in empty


def test_help_explains_fields_and_badges(capsys):
    assert cli.main(["catalog", "show", "--help"]) == 0
    help_text = capsys.readouterr().out
    assert "--format" in help_text
    assert "--legend" in help_text
    assert "google_apis" in help_text
    assert "unmapped_caller" in help_text


def _update() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    saved = catalog_format.os.environ.pop("NO_COLOR", None)
    try:
        for fixture_name, argv in GOLDEN_CASES:
            code, output = _run_cli(*argv)
            if code != 0:
                raise RuntimeError(f"{argv} failed with {code}")
            (FIXTURES / fixture_name).write_text(output)
            print(f"wrote {(FIXTURES / fixture_name).relative_to(REPO)}")
    finally:
        if saved is not None:
            catalog_format.os.environ["NO_COLOR"] = saved


if __name__ == "__main__":  # pragma: no cover - maintenance entry point
    if sys.argv[1:] != ["--update"]:
        sys.exit("usage: python packages/esmeralda/tests/test_catalog_show_format.py --update")
    _update()
