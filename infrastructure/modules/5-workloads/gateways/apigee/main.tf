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

# Delegates to `modules/5-workloads/apigee/platform`, which implements the
# non-VPC-peering Private Service Connect Apigee X platform and entrypoint
# gateway contract.

module "platform" {
  source = "../../apigee/platform"

  project_id                           = var.project_id
  region                               = var.region
  environment                          = var.environment
  vpc_id                               = var.vpc_id
  subnet_id                            = var.subnet_id
  apigee_env_name                      = "esmeralda-${var.environment}"
  environment_type                     = var.environment_type
  byo_apigee_org                       = var.byo_apigee_org
  existing_org_id                      = var.existing_org_id
  existing_instance_service_attachment = var.existing_instance_service_attachment
}
