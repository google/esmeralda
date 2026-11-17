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

"""Keeps the offline test suites hermetic: no Google credentials of the developer (or the CI runner).

Several tests run `terraform`/`tofu apply` on modules that declare the google provider. They only
create `terraform_data` resources, but Terraform still configures the provider, which looks for
Application Default Credentials. The real credential variables are removed and the gcloud config
directory points to an empty temp dir, so ADC is never found; a dummy access token lets the provider
configure offline. No test calls a Google API: a test that tried would fail with this token.
"""

from __future__ import annotations

import tempfile

import pytest

_CREDENTIAL_VARS = (
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CREDENTIALS",
    "GOOGLE_CLOUD_KEYFILE_JSON",
    "GCLOUD_KEYFILE_JSON",
    "GOOGLE_OAUTH_ACCESS_TOKEN",
    "GOOGLE_IMPERSONATE_SERVICE_ACCOUNT",
    "CLOUDSDK_AUTH_ACCESS_TOKEN_FILE",
    "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
)

DUMMY_ACCESS_TOKEN = "offline-test-token"  # not a real token: any API call with it fails


@pytest.fixture(autouse=True, scope="session")
def _no_google_credentials():
    with (
        tempfile.TemporaryDirectory(prefix="esmeralda-tests-gcloud-") as empty_config,
        pytest.MonkeyPatch.context() as mp,
    ):
        for name in _CREDENTIAL_VARS:
            mp.delenv(name, raising=False)
        mp.setenv("CLOUDSDK_CONFIG", empty_config)
        mp.setenv("GOOGLE_OAUTH_ACCESS_TOKEN", DUMMY_ACCESS_TOKEN)
        yield
