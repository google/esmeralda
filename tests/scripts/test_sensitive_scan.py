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
"""Sensitive-content scan (scripts/sensitive_scan.py): built-in rules, local patterns, diff parsing.

Secret-looking strings are assembled at runtime so this file does not trip the scan itself.
"""

import re

import sensitive_scan as ss

DASHES = "-" * 5
PEM = f"{DASHES}BEGIN RSA PRIVATE KEY{DASHES}"
API_KEY = "AI" + "za" + "Sy" + "A" * 33
GH_TOKEN = "gh" + "p_" + "a1B2" * 9


def rules(*local):
    extra = [(f"local pattern #{i}", re.compile(p, re.IGNORECASE)) for i, p in enumerate(local, 1)]
    return ss.BUILTIN_CONTENT_RULES + extra


def diff_for(path, *added):
    body = "\n".join("+" + line for line in added)
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +10,{len(added)} @@\n{body}\n"


def test_builtin_content_rules_match():
    for text in (PEM, f'"private_key": "{DASHES}BEGIN', API_KEY, GH_TOKEN, "ya29." + "x" * 30, "AKIA" + "A" * 16):
        assert ss.scan_text("f:1", text, ss.BUILTIN_CONTENT_RULES), text


def test_builtin_rules_ignore_ordinary_code():
    for text in ("api_key = os.environ['API_KEY']", "token = get_token()", "BEGIN TRANSACTION", "# sha256 digest"):
        assert not ss.scan_text("f:1", text, ss.BUILTIN_CONTENT_RULES), text


def test_findings_are_masked():
    (finding,) = ss.scan_text("f:1", f"key={API_KEY}", ss.BUILTIN_CONTENT_RULES)
    assert API_KEY not in str(finding) and "chars" in finding.snippet


def test_path_rules():
    blocked = [
        "infrastructure/live/dev/terraform.tfvars",
        "x/prod.tfvars.json",
        "a/terraform.tfstate",
        "a/terraform.tfstate.backup",
        ".env",
        "apps/x/.env.local",
        "plan.tfplan",
        "certs/server.key",
        "apps/agents/x/.adk/session.db",
        "infrastructure/live/.local_states",
        ".plans/dev/layer-x/plan.json",
    ]
    for path in blocked:
        assert ss.check_path(path), path
    for path in (".env.example", "scripts/env_status.py", "docs/src/state.md", "main.tf"):
        assert not ss.check_path(path), path


def test_parse_added_lines_tracks_paths_and_numbers():
    diff = diff_for("a.py", "one", "two") + diff_for("b.md", "three")
    assert ss.parse_added_lines(diff) == [("a.py", 10, "one"), ("a.py", 11, "two"), ("b.md", 10, "three")]


def test_scan_diff_with_local_patterns():
    diff = diff_for("docs/x.md", "Deployed for ACME Corp last week", "nothing here")
    findings = ss.scan_diff(diff, ["docs/x.md"], rules(r"acme\s+corp"))
    assert [f.where for f in findings] == ["docs/x.md:10"]
    assert findings[0].rule == "local pattern #1"


def test_local_patterns_also_match_file_names():
    findings = ss.scan_diff("", ["docs/acme-notes.md"], rules("acme"))
    assert findings and findings[0].where == "docs/acme-notes.md"


def test_load_patterns(tmp_path):
    f = tmp_path / "patterns"
    f.write_text("# comment\n\nacme\n  internal-host\\.example  \n")
    loaded = ss.load_patterns(f)
    assert [name for name, _ in loaded] == ["local pattern #3", "local pattern #4"]
    assert loaded[0][1].search("ACME")  # case-insensitive
    assert ss.load_patterns(tmp_path / "missing") == []


def test_invalid_pattern_is_reported(tmp_path):
    f = tmp_path / "patterns"
    f.write_text("(unclosed\n")
    try:
        ss.load_patterns(f)
    except SystemExit as exc:
        assert "invalid regex" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_patterns_file_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ESMERALDA_SENSITIVE_PATTERNS_FILE", str(tmp_path / "p"))
    assert ss.patterns_file() == tmp_path / "p"


def test_message_file_and_skip(monkeypatch, tmp_path, capsys):
    msg = tmp_path / "MSG"
    msg.write_text(f"feat: add key\n# {PEM} in a comment line is ignored\n{GH_TOKEN}\n")
    monkeypatch.setenv("ESMERALDA_SENSITIVE_PATTERNS_FILE", str(tmp_path / "none"))
    assert ss.main(["--message-file", str(msg)]) == 1
    out = capsys.readouterr().out
    assert "commit message:3" in out and "commit message:2" not in out
    monkeypatch.setenv("SKIP_SENSITIVE_SCAN", "1")
    assert ss.main(["--message-file", str(msg)]) == 0
