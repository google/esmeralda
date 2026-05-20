# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

module "gcs" {
  source = "./gcs"

  project_id   = var.project_id
  region       = var.region
  bucket_names = var.bucket_names
}

module "bigquery_logging" {
  source = "./bigquery-logging"

  project_id     = var.project_id
  project_number = var.project_number
  region         = var.region
}
