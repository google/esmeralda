# Copyright 2025 Google LLC
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

.PHONY: deploy deploy-python deploy-terraform deploy-python-cb deploy-terraform-cb help

MODE ?= python
AGENT_FILTER ?=
USE_CLOUDBUILD ?= false

help:
	@echo "Available deployment targets:"
	@echo "  make deploy-python       Deploy agents locally via standard Python SDK"
	@echo "  make deploy-terraform    Deploy agents locally via declarative Terraform"
	@echo "  make deploy-python-cb    Deploy agents via Cloud Build using Python SDK"
	@echo "  make deploy-terraform-cb Deploy agents via Cloud Build using Terraform"
	@echo "  make deploy MODE=...     Deploy with specified MODE (python or terraform)"

deploy:
	@DEPLOY_MODE=$(MODE) USE_CLOUDBUILD=$(USE_CLOUDBUILD) AGENT_FILTER=$(AGENT_FILTER) bash deploy.sh

deploy-python:
	@DEPLOY_MODE=python USE_CLOUDBUILD=false AGENT_FILTER=$(AGENT_FILTER) bash deploy.sh

deploy-terraform:
	@DEPLOY_MODE=terraform USE_CLOUDBUILD=false AGENT_FILTER=$(AGENT_FILTER) bash deploy.sh

deploy-python-cb:
	@DEPLOY_MODE=python USE_CLOUDBUILD=true AGENT_FILTER=$(AGENT_FILTER) bash deploy.sh

deploy-terraform-cb:
	@DEPLOY_MODE=terraform USE_CLOUDBUILD=true AGENT_FILTER=$(AGENT_FILTER) bash deploy.sh
