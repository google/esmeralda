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

"""Agent Gateway service agent grants the Agent Connectivity Template validation needs.

Without compute.networkAttachments.get on the network attachment (governance project) and
roles/dns.reader on the DNS peering target network's project, creating the ACT fails with
"Config validation failed".
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
AGW_MAIN_TF = REPO_ROOT / "infrastructure/modules/4-governance/modules/6_agent_gateway/main.tf"


def _block(content: str, header: str) -> str:
    start = content.index(header)
    depth = 0
    for i in range(content.index("{", start), len(content)):
        depth += {"{": 1, "}": -1}.get(content[i], 0)
        if depth == 0:
            return content[start : i + 1]
    raise AssertionError(f"unterminated block {header}")


def test_network_attachment_reader_is_a_one_permission_custom_role():
    content = AGW_MAIN_TF.read_text()
    role = _block(content, 'resource "google_project_iam_custom_role" "agent_gateway_network_attachment_reader"')
    assert "project     = var.governance_project_id" in role
    assert 'permissions = ["compute.networkAttachments.get"]' in role
    grant = _block(content, 'resource "google_project_iam_member" "agent_gateway_network_attachment_reader"')
    assert "role    = google_project_iam_custom_role.agent_gateway_network_attachment_reader[0].name" in grant
    assert "@gcp-sa-agentgateway.iam.gserviceaccount.com" in grant
    # Never the broad predefined role.
    assert "roles/compute.networkViewer\"" not in content


def test_dns_reader_on_the_dns_peering_target_project():
    content = AGW_MAIN_TF.read_text()
    grant = _block(content, 'resource "google_project_iam_member" "agent_gateway_dns_reader"')
    assert 'role    = "roles/dns.reader"' in grant
    assert "project = local.agent_gateway_dns_project" in grant
    assert "@gcp-sa-agentgateway.iam.gserviceaccount.com" in grant
    assert re.search(
        r'agent_gateway_dns_project = startswith\(var\.vpc_name, "projects/"\) \? split\("/", var\.vpc_name\)\[1\] : var\.net_host_project_id',
        content,
    )


def test_act_waits_for_every_service_agent_grant():
    content = AGW_MAIN_TF.read_text()
    act = _block(content, 'resource "google_network_services_agent_connectivity_template" "egress"')
    depends_on = act.split("depends_on = [", 1)[1].split("]", 1)[0]
    for dep in (
        "google_project_iam_member.agent_gateway_dns_peer",
        "google_project_iam_member.agent_gateway_network_user",
        "google_project_iam_member.agent_gateway_network_attachment_reader",
        "google_project_iam_member.agent_gateway_dns_reader",
        "time_sleep.agent_gateway_iam_propagation",
    ):
        assert dep in depends_on, dep
    sleep = _block(content, 'resource "time_sleep" "agent_gateway_iam_propagation"')
    assert re.search(r'create_duration = "\d+s"', sleep)
    for trigger in ("network_attachment_reader", "dns_reader", "dns_peer", "network_user"):
        assert re.search(rf"^\s*{trigger}\s*=", sleep, re.M), trigger
