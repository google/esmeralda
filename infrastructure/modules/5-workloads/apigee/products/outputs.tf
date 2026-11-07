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

output "product_names" {
  description = "Map of product keys to provisioned Apigee API product names."
  value       = { for k, p in google_apigee_api_product.product : k => p.name }
}

output "consumer_app_ids" {
  description = "Map of consumer keys to provisioned Apigee developer app IDs."
  value       = { for k, app in google_apigee_developer_app.consumer : k => app.app_id }
}

output "consumer_key_secret_ids" {
  description = "Map of consumer keys to Secret Manager secret resource IDs storing the generated API keys."
  value       = { for k, s in google_secret_manager_secret.consumer_key : k => s.id }
}

output "consumer_key_secret_names" {
  description = "Map of consumer keys to Secret Manager short secret_id names (`apigee-consumer-key-<consumer>-<env>`)."
  value       = { for k, s in google_secret_manager_secret.consumer_key : k => s.secret_id }
}
