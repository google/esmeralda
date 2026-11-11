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

"""Shared fixtures: the ``esmeralda.usage`` contract (``contracts/usage-event.schema.json``)."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts"
USAGE_SCHEMA_PATH = CONTRACTS / "usage-event.schema.json"


@pytest.fixture(scope="session")
def usage_schema():
    return json.loads(USAGE_SCHEMA_PATH.read_text())


@pytest.fixture(scope="session")
def usage_validator(usage_schema):
    return Draft202012Validator(usage_schema)


@pytest.fixture(scope="session")
def assert_valid_usage(usage_validator):
    """Asserts that a record follows the usage contract, listing every violation."""

    def check(record):
        errors = sorted(usage_validator.iter_errors(record), key=lambda e: list(e.absolute_path))
        assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors)
        return record

    return check
