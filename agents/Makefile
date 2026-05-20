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

.PHONY: agents

# Extract any arguments passed after "agents"
ifeq (agents,$(firstword $(MAKECMDGOALS)))
  # Use the rest as arguments
  AGENT_ARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  # Turn them into do-nothing targets
  $(eval $(AGENT_ARGS):;@:)
endif

agents:
	@if [ -n "$(AGENT_ARGS)" ]; then \
		bash deploy.sh $(AGENT_ARGS); \
	else \
		bash deploy.sh; \
	fi
