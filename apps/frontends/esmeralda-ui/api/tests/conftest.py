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

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from ui_testkit import AUDIENCE, REPO_ROOT, KeyPair

from esmeralda_ui.settings import Settings


@pytest.fixture(scope="session")
def keypair() -> KeyPair:
    return KeyPair()


@pytest.fixture
def cloud_settings() -> Callable[..., Settings]:
    def make(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "mode": "cloud",
            "iap_audience": AUDIENCE,
            "catalog_root": REPO_ROOT,
            "environment": "dev",
        }
        values.update(overrides)
        return Settings(**values)

    return make
