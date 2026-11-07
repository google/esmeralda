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
"""Plan summary converter and apply guard (scripts/tf_plan.py), on synthetic plan JSON."""

import json

import pytest
import tf_plan


def rc(address, actions, before=None, after=None, **extra):
    change = {"actions": actions, "before": before, "after": after, "after_unknown": extra.pop("after_unknown", {})}
    change.update(extra.pop("change", {}))
    return {"address": address, "change": change, **extra}


NO_CHANGES = {
    "format_version": "1.2",
    "resource_changes": [rc("google_project.p", ["no-op"], {"name": "a"}, {"name": "a"})],
    "output_changes": {"project_id": {"actions": ["no-op"]}},
}

MIXED = {
    "format_version": "1.2",
    "resource_changes": [
        rc("google_storage_bucket.new", ["create"], None, {"name": "b"}),
        rc(
            "module.run.google_cloud_run_v2_service.svc",
            ["update"],
            {"name": "s", "labels": {"a": "1"}, "template": [{"image": "x@sha256:1"}], "uri": "u"},
            {"name": "s", "labels": {"a": "2"}, "template": [{"image": "x@sha256:2"}], "uri": None},
            after_unknown={"uri": True, "template": [{}], "labels": {}},
        ),
        rc(
            "google_compute_instance.vm",
            ["delete", "create"],
            {"zone": "a", "name": "vm"},
            {"zone": "b", "name": "vm"},
            change={"replace_paths": [["zone"]]},
            action_reason="replace_because_cannot_update",
        ),
        rc("google_dns_record_set.old", ["delete"], {"name": "r"}, None),
        rc("data.google_client_config.me", ["read"], None, {}),
        rc("google_secret.moved", ["no-op"], {"a": 1}, {"a": 1}, previous_address="google_secret.old"),
    ],
    "output_changes": {
        "service_uri": {"actions": ["update"]},
        "new_output": {"actions": ["create"]},
        "same": {"actions": ["no-op"]},
    },
    "resource_drift": [{"address": "google_project.p"}],
}


def test_action_of():
    assert tf_plan.action_of(["create"]) == "create"
    assert tf_plan.action_of(["delete", "create"]) == "replace"
    assert tf_plan.action_of(["create", "delete"]) == "replace"
    assert tf_plan.action_of(["no-op"]) == "no-op"
    assert tf_plan.action_of(["read"]) == "read"
    assert tf_plan.action_of([]) == "no-op"


def test_no_changes():
    summary = tf_plan.summarize(NO_CHANGES)
    assert not summary.has_changes
    assert tf_plan.render(summary, "dev/unit") == "📋 dev/unit: No changes."


def test_mixed_plan_lines_and_counts():
    summary = tf_plan.summarize(MIXED)
    assert summary.counts() == {"create": 1, "update": 1, "replace": 1, "delete": 1, "read": 1, "move": 1}
    by_addr = {r.address: r for r in summary.resources}
    # updates list the changed top-level attributes (values are never shown)
    assert by_addr["module.run.google_cloud_run_v2_service.svc"].attrs == ["labels", "template", "uri"]
    # replaces list what forces the replacement, and why
    vm = by_addr["google_compute_instance.vm"]
    assert vm.action == "replace" and vm.attrs == ["zone"] and "cannot update" in vm.note
    assert by_addr["google_secret.moved"].note == "moved from google_secret.old"
    assert summary.outputs == [("new_output", "create"), ("service_uri", "update")]
    assert summary.drift == 1

    text = tf_plan.render(summary, "dev/x")
    lines = text.splitlines()
    assert lines[0].startswith("📋 dev/x: 1 create, 1 update, 1 replace, 1 delete, 1 read, 1 move")
    # destructive changes first
    assert "-/+" in lines[1] and "google_compute_instance.vm" in lines[1]
    assert "  - " in lines[2] or lines[2].lstrip().startswith("-")
    assert "outputs: +new_output, ~service_uri" in text
    assert "x@sha256" not in text  # no attribute values leak into the summary


def test_output_only_change_counts_as_change():
    plan = {"resource_changes": [], "output_changes": {"o": {"actions": ["create"]}}}
    summary = tf_plan.summarize(plan)
    assert summary.has_changes
    assert "outputs only" in tf_plan.render(summary, "u")


def test_sensitive_only_change_detected():
    change = {
        "actions": ["update"],
        "before": {"pw": "same"},
        "after": {"pw": "same"},
        "before_sensitive": {},
        "after_sensitive": {"pw": True},
    }
    assert tf_plan.changed_attributes(change) == ["pw"]


def test_summarize_cli_exit_codes(tmp_path, capsys):
    f = tmp_path / "plan.json"
    f.write_text(json.dumps(NO_CHANGES))
    assert tf_plan.main(["summarize", str(f)]) == 0
    f.write_text(json.dumps(MIXED))
    assert tf_plan.main(["summarize", str(f), "--label", "dev/x"]) == 2
    f.write_text(json.dumps({**NO_CHANGES, "errored": True}))
    assert tf_plan.main(["summarize", str(f)]) == 1
    f.write_text("not json")
    assert tf_plan.main(["summarize", str(f)]) == 1


# --- apply guard ---------------------------------------------------------------------------------


def test_guard_refuses_missing_plan():
    assert "no saved plan" in tf_plan.apply_guard(None, allow_destroy=False)


def test_guard_refuses_destroy_without_flag():
    summary = tf_plan.summarize(MIXED)
    reason = tf_plan.apply_guard(summary, allow_destroy=False)
    assert "ALLOW_DESTROY=1" in reason
    assert "google_compute_instance.vm (replace)" in reason and "google_dns_record_set.old (delete)" in reason
    assert tf_plan.apply_guard(summary, allow_destroy=True) is None


def test_guard_allows_safe_and_empty_plans():
    assert tf_plan.apply_guard(tf_plan.summarize(NO_CHANGES), allow_destroy=False) is None
    plan = {"resource_changes": [rc("a.b", ["create"], None, {})]}
    assert tf_plan.apply_guard(tf_plan.summarize(plan), allow_destroy=False) is None


def test_guard_refuses_errored_plan():
    assert "errored" in tf_plan.apply_guard(tf_plan.summarize({"errored": True}), allow_destroy=True)


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    unit = tmp_path / "infrastructure" / "live" / "dev" / "layer-x"
    unit.mkdir(parents=True)
    (unit / "terragrunt.hcl").write_text("")
    monkeypatch.setattr(tf_plan, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tf_plan, "LIVE_DIR", tmp_path / "infrastructure" / "live")
    monkeypatch.setattr(tf_plan, "PLANS_DIR", tmp_path / ".plans")
    calls = []
    monkeypatch.setattr(tf_plan, "_run", lambda cmd, cwd, log: calls.append(cmd) or log.write_text("") or 0)
    return tmp_path, calls


def _save_plan(root, plan):
    out = root / ".plans" / "dev" / "layer-x"
    out.mkdir(parents=True)
    (out / "plan.tfplan").write_bytes(b"binary")
    (out / "plan.json").write_text(json.dumps(plan))
    return out


def test_apply_refuses_missing_plan_without_running_terragrunt(fake_repo, capsys):
    _, calls = fake_repo
    assert tf_plan.main(["apply", "--env", "dev", "--unit", "layer-x"]) == 1
    assert calls == []
    assert "no saved plan" in capsys.readouterr().out


def test_apply_refuses_destructive_plan(fake_repo, capsys):
    root, calls = fake_repo
    _save_plan(root, MIXED)
    assert tf_plan.main(["apply", "--env", "dev", "--unit", "layer-x"]) == 1
    assert calls == []
    out = capsys.readouterr().out
    assert out.index("📋") < out.index("⛔")  # summary printed before the refusal


def test_apply_runs_only_the_saved_plan_and_consumes_it(fake_repo):
    root, calls = fake_repo
    out = _save_plan(root, MIXED)
    assert tf_plan.main(["apply", "--env", "dev", "--unit", "layer-x", "--allow-destroy"]) == 0
    assert len(calls) == 1 and calls[0][:2] == ["terragrunt", "apply"] and calls[0][-1] == str(out / "plan.tfplan")
    assert "-auto-approve" not in calls[0]
    assert not (out / "plan.tfplan").exists()


def test_unknown_unit_is_an_error(fake_repo):
    assert tf_plan.main(["apply", "--env", "dev", "--unit", "nope"]) == 1


def test_plan_body_drops_init_and_refresh_noise():
    text = (
        "Initializing the backend...\n- Reusing hashicorp/google\nTerraform has been successfully initialized!\n"
        "google_x.a: Refreshing state... [id=a]\ndata.google_y.b: Reading...\ndata.google_y.b: Read complete after 1s\n"
        '\nTerraform will perform the following actions:\n  ~ resource "google_x" "a" {\n'
    )
    body = tf_plan.plan_body(text)
    assert body.startswith("Terraform will perform")
    assert "Refreshing" not in body and "Initializing" not in body and "Read complete" not in body
