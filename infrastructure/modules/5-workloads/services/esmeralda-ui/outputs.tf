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

output "service_name" {
  description = "Cloud Run service name for esmeralda-ui."
  value       = google_cloud_run_v2_service.esmeralda_ui.name
}

output "service_uri" {
  description = "Direct Cloud Run .run.app HTTPS URL (protected by Direct Cloud Run IAP when iap_enabled = true)."
  value       = google_cloud_run_v2_service.esmeralda_ui.uri
}

output "service_account_email" {
  description = "Service account email used by esmeralda-ui."
  value       = google_service_account.ui_sa.email
}
