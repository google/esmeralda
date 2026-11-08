#!/usr/bin/env bash
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
#
# Migrate Layer 5 Terraform/Terragrunt states for the data/cloud-sql extraction
# and top-level iap-egress / test-vm layout flattening without destroying any
# live resources:
#   1. Copy/move unit states whose path_relative_to_include() key changed in root.hcl:
#        <env>/layer-5-workloads/services/iap-egress -> <env>/layer-5-workloads/iap-egress
#        <env>/layer-5-workloads/services/test-vm    -> <env>/layer-5-workloads/test-vm
#   2. Extract the stateful Cloud SQL resources out of:
#        <env>/layer-5-workloads/agents/ai-coe-mortgage-specialist/terraform.tfstate
#      into:
#        <env>/layer-5-workloads/data/cloud-sql/terraform.tfstate
#      mapping unindexed resources to count-indexed [0] addresses:
#        - google_sql_database_instance.task_store      -> google_sql_database_instance.task_store[0]
#        - google_sql_database.tasks_db                 -> google_sql_database.agent_tasks[0]
#        - google_sql_user.agent_iam_user               -> google_sql_user.agent_user[0]
#        - google_sql_user.postgres_user                -> google_sql_user.postgres_user[0]
#        - random_password.postgres_pwd                 -> random_password.postgres_pwd[0]
#        - google_cloud_run_v2_job.schema_bootstrap     -> google_cloud_run_v2_job.schema_bootstrap[0]
#        - google_project_iam_member.cloudsql_client_role -> google_project_iam_member.cloudsql_client_role[0]
#        - google_project_iam_member.cloudsql_user_role   -> google_project_iam_member.cloudsql_user_role[0]
#        - google_secret_manager_secret.db_Connection   -> google_secret_manager_secret.db_Connection[0]
#        - google_secret_manager_secret_version.db_Connection_val -> google_secret_manager_secret_version.db_Connection_val[0]
#        - null_resource.db_ready                       -> terraform_data.db_ready[0]
#        - null_resource.trigger_bootstrap              -> terraform_data.schema_bootstrap[0]
#
# Usage:
#   bash scripts/migrate_cloud_sql_state.sh [ENV=dev] [--gcs-bucket BUCKET]
set -euo pipefail

ENV="${1:-${ENV:-dev}}"
GCS_BUCKET="${GCS_BUCKET:-}"
if [[ "${2:-}" == "--gcs-bucket" && -n "${3:-}" ]]; then
  GCS_BUCKET="$3"
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATES_ROOT="${STATES_ROOT:-${REPO_ROOT}/infrastructure/live/.local_states}"
ENV_STATE_DIR="${STATES_ROOT}/${ENV}"

if [[ -n "${GCS_BUCKET}" ]]; then
  echo "☁️  Pulling ${ENV} Layer 5 states from gs://${GCS_BUCKET}/${ENV}/ into ${ENV_STATE_DIR}..."
  mkdir -p "${ENV_STATE_DIR}"
  gcloud storage rsync --recursive "gs://${GCS_BUCKET}/${ENV}" "${ENV_STATE_DIR}"
fi

if [[ ! -d "${ENV_STATE_DIR}" ]]; then
  echo "ℹ️  No local state directory found at ${ENV_STATE_DIR}; nothing to migrate."
  exit 0
fi

python3 - "${ENV_STATE_DIR}" <<'PY'
import copy
import json
import pathlib
import shutil
import sys
import uuid

env_dir = pathlib.Path(sys.argv[1])
l5_dir = env_dir / "layer-5-workloads"

# 1. Copy path-relocated unit states (services/{iap-egress,test-vm} -> {iap-egress,test-vm})
for unit in ("iap-egress", "test-vm"):
    old_state = l5_dir / "services" / unit / "terraform.tfstate"
    new_state = l5_dir / unit / "terraform.tfstate"
    if old_state.is_file() and not new_state.is_file():
        new_state.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_state, new_state)
        print(f"✅ Copied state: services/{unit}/terraform.tfstate -> {unit}/terraform.tfstate")
    elif new_state.is_file():
        print(f"ℹ️  State already present at {unit}/terraform.tfstate")

# 2. Extract Cloud SQL resources from agents/ai-coe-mortgage-specialist -> data/cloud-sql
agent_state_path = l5_dir / "agents" / "ai-coe-mortgage-specialist" / "terraform.tfstate"
cloud_sql_state_path = l5_dir / "data" / "cloud-sql" / "terraform.tfstate"

if not agent_state_path.is_file():
    print(f"ℹ️  No agent state found at {agent_state_path}; skipping Cloud SQL extraction.")
    sys.exit(0)

agent_state = json.loads(agent_state_path.read_text())

# Map (type, old_name) -> (new_type, new_name)
DIRECT_MOVE_MAP = {
    ("google_sql_database_instance", "task_store"): ("google_sql_database_instance", "task_store"),
    ("google_sql_database", "tasks_db"): ("google_sql_database", "agent_tasks"),
    ("google_sql_database", "agent_tasks"): ("google_sql_database", "agent_tasks"),
    ("google_sql_user", "agent_iam_user"): ("google_sql_user", "agent_user"),
    ("google_sql_user", "agent_user"): ("google_sql_user", "agent_user"),
    ("google_sql_user", "postgres_user"): ("google_sql_user", "postgres_user"),
    ("random_password", "postgres_pwd"): ("random_password", "postgres_pwd"),
    ("google_cloud_run_v2_job", "schema_bootstrap"): ("google_cloud_run_v2_job", "schema_bootstrap"),
    ("google_project_iam_member", "cloudsql_client_role"): ("google_project_iam_member", "cloudsql_client_role"),
    ("google_project_iam_member", "cloudsql_user_role"): ("google_project_iam_member", "cloudsql_user_role"),
    ("google_secret_manager_secret", "db_Connection"): ("google_secret_manager_secret", "db_Connection"),
    ("google_secret_manager_secret_version", "db_Connection_val"): ("google_secret_manager_secret_version", "db_Connection_val"),
}

NULL_TO_TERRAFORM_DATA = {
    ("null_resource", "db_ready"): "db_ready",
    ("null_resource", "trigger_bootstrap"): "schema_bootstrap",
    ("terraform_data", "db_ready"): "db_ready",
    ("terraform_data", "schema_bootstrap"): "schema_bootstrap",
}

DEP_RENAME_MAP = {
    "google_sql_database.tasks_db": "google_sql_database.agent_tasks",
    "google_sql_user.agent_iam_user": "google_sql_user.agent_user",
    "null_resource.db_ready": "terraform_data.db_ready",
    "null_resource.trigger_bootstrap": "terraform_data.schema_bootstrap",
}

EXTRACTED_ADDRS = {
    f"{t}.{n}" for (t, n) in list(DIRECT_MOVE_MAP.keys()) + list(NULL_TO_TERRAFORM_DATA.keys())
}

moved_resources = []
remaining_resources = []

for res in agent_state.get("resources", []):
    key = (res.get("type"), res.get("name"))
    if res.get("mode") == "managed" and key in DIRECT_MOVE_MAP:
        new_type, new_name = DIRECT_MOVE_MAP[key]
        new_res = copy.deepcopy(res)
        new_res["type"] = new_type
        new_res["name"] = new_name
        for inst in new_res.get("instances", []):
            inst["index_key"] = 0
            if "dependencies" in inst and inst["dependencies"]:
                inst["dependencies"] = [DEP_RENAME_MAP.get(d, d) for d in inst["dependencies"]]
        moved_resources.append(new_res)
    elif res.get("mode") == "managed" and key in NULL_TO_TERRAFORM_DATA:
        td_name = NULL_TO_TERRAFORM_DATA[key]
        old_inst = (res.get("instances") or [{}])[0]
        old_id = old_inst.get("attributes", {}).get("id") or str(uuid.uuid4())
        deps = [DEP_RENAME_MAP.get(d, d) for d in (old_inst.get("dependencies") or [])]
        moved_resources.append(
            {
                "mode": "managed",
                "type": "terraform_data",
                "name": td_name,
                "provider": 'provider["terraform.io/builtin/terraform"]',
                "instances": [
                    {
                        "index_key": 0,
                        "schema_version": 0,
                        "attributes": {
                            "id": str(old_id),
                            "input": None,
                            "output": None,
                            "triggers_replace": None,
                        },
                        "sensitive_attributes": [],
                        "dependencies": deps,
                    }
                ],
            }
        )
    else:
        clean_res = copy.deepcopy(res)
        for inst in clean_res.get("instances", []):
            if "dependencies" in inst and inst["dependencies"]:
                inst["dependencies"] = [
                    d for d in inst["dependencies"] if d not in EXTRACTED_ADDRS
                ]
        remaining_resources.append(clean_res)

if not moved_resources and cloud_sql_state_path.is_file():
    print("ℹ️  Cloud SQL resources already migrated to data/cloud-sql/terraform.tfstate")
    sys.exit(0)

# Extract connection attributes from task_store and agent_tasks for outputs
conn_name = ""
priv_ip = ""
db_name = "a2a_tasks"
db_secret_id = ""
for r in moved_resources:
    attrs = (r.get("instances") or [{}])[0].get("attributes", {})
    if r["type"] == "google_sql_database_instance":
        conn_name = attrs.get("connection_name", "")
        priv_ip = attrs.get("private_ip_address", "")
    elif r["type"] == "google_sql_database":
        db_name = attrs.get("name", db_name)
    elif r["type"] == "google_secret_manager_secret":
        db_secret_id = attrs.get("id", "")

cloud_sql_state = {
    "version": 4,
    "terraform_version": agent_state.get("terraform_version", "1.14.8"),
    "serial": 1,
    "lineage": str(uuid.uuid4()),
    "outputs": {
        "enabled": {"value": True, "type": "bool"},
        "instance_connection_name": {"value": conn_name, "type": "string"},
        "db_secret_id": {"value": db_secret_id, "type": "string"},
        "db_private_ip": {"value": priv_ip, "type": "string"},
        "database_name": {"value": db_name, "type": "string"},
    },
    "resources": moved_resources,
    "check_results": None,
}

# Backup original agent state before writing
backup_path = agent_state_path.with_suffix(".tfstate.pre-cloudsql-split.bak")
if not backup_path.exists():
    shutil.copy2(agent_state_path, backup_path)

cloud_sql_state_path.parent.mkdir(parents=True, exist_ok=True)
cloud_sql_state_path.write_text(json.dumps(cloud_sql_state, indent=2) + "\n")

agent_state["resources"] = remaining_resources
agent_state["serial"] = int(agent_state.get("serial", 1)) + 1
if "outputs" in agent_state:
    agent_state["outputs"].pop("db_private_ip", None)
agent_state_path.write_text(json.dumps(agent_state, indent=2) + "\n")

print(
    f"✅ Extracted {len(moved_resources)} Cloud SQL resources into data/cloud-sql/terraform.tfstate "
    f"({len(remaining_resources)} resources remain in agents/ai-coe-mortgage-specialist/terraform.tfstate)"
)
PY

if [[ -n "${GCS_BUCKET}" ]]; then
  echo "☁️  Pushing migrated ${ENV} states back to gs://${GCS_BUCKET}/${ENV}/..."
  for unit in "data/cloud-sql" "agents/ai-coe-mortgage-specialist" "iap-egress" "test-vm"; do
    src="${ENV_STATE_DIR}/layer-5-workloads/${unit}/terraform.tfstate"
    if [[ -f "${src}" ]]; then
      gcloud storage cp "${src}" "gs://${GCS_BUCKET}/${ENV}/layer-5-workloads/${unit}/terraform.tfstate"
    fi
  done
fi
