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

"""Configuration of the BFF, from ``ESMERALDA_UI_*`` environment variables (or the CLI).

Nothing environment-specific is hardcoded: the BigQuery project and dataset, the IAP audience and the
catalog environment all come from configuration. Validation fails closed:

* ``cloud`` mode (the default) requires an IAP audience; every request must carry a valid IAP JWT.
* ``local`` mode uses a fixed, clearly labelled development identity. It must be asked for
  explicitly (``esmeralda ui --local``) and refuses to start on Cloud Run (``K_SERVICE`` set).
* ``demo`` serves synthetic usage, audit and trace data, labelled ``source = demo`` in every
  response (the web shows a "Demo data" badge). It is never a fallback: it is on only when asked for.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

PREFIX = "ESMERALDA_UI_"
MODES = ("cloud", "local")

# BigQuery identifiers are interpolated into SQL as quoted table names (values always go through
# query parameters), so they are validated strictly here.
PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
DATASET_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")
ENV_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
LOCATION_RE = re.compile(r"^[A-Za-z0-9-]{2,40}$")

DEFAULT_MAX_BYTES_BILLED = 1 << 30  # 1 GiB per query
MAX_MAX_BYTES_BILLED = 100 << 30
LOCAL_IDENTITY_EMAIL = "local-developer@localhost"
LOCAL_IDENTITY_SUBJECT = "local-dev"


class SettingsError(ValueError):
    """The configuration is invalid; the BFF refuses to start."""


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    mode: str = "cloud"
    demo: bool = False
    environment: str = "dev"
    catalog_root: Path | None = None
    iap_audience: str | None = None
    bq_project: str | None = None
    bq_dataset: str | None = None
    bq_location: str | None = None
    bq_max_bytes_billed: int = DEFAULT_MAX_BYTES_BILLED
    static_dir: Path | None = None

    def __post_init__(self) -> None:
        problems = []
        if self.mode not in MODES:
            problems.append(f"mode must be one of {', '.join(MODES)} (got {self.mode!r})")
        if self.mode == "cloud" and not (self.iap_audience or "").strip():
            problems.append(f"{PREFIX}IAP_AUDIENCE is required in cloud mode (every request is verified)")
        if not ENV_RE.match(self.environment):
            problems.append(f"invalid environment name {self.environment!r}")
        if bool(self.bq_project) != bool(self.bq_dataset):
            problems.append(f"set both {PREFIX}BQ_PROJECT and {PREFIX}BQ_DATASET, or neither")
        if self.bq_project and not PROJECT_RE.match(self.bq_project):
            problems.append(f"invalid BigQuery project id {self.bq_project!r}")
        if self.bq_dataset and not DATASET_RE.match(self.bq_dataset):
            problems.append(f"invalid BigQuery dataset id {self.bq_dataset!r}")
        if self.bq_location and not LOCATION_RE.match(self.bq_location):
            problems.append(f"invalid BigQuery location {self.bq_location!r}")
        if not 0 < self.bq_max_bytes_billed <= MAX_MAX_BYTES_BILLED:
            problems.append(f"{PREFIX}BQ_MAX_BYTES_BILLED must be between 1 and {MAX_MAX_BYTES_BILLED}")
        if problems:
            raise SettingsError("; ".join(problems))

    @property
    def local(self) -> bool:
        return self.mode == "local"

    @property
    def bigquery_configured(self) -> bool:
        return bool(self.bq_project and self.bq_dataset)

    @property
    def data_source(self) -> str:
        """``demo`` (explicit), ``bigquery`` (configured) or ``unconfigured`` (data endpoints answer 503)."""
        if self.demo:
            return "demo"
        return "bigquery" if self.bigquery_configured else "unconfigured"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, **overrides: object) -> Settings:
        env = os.environ if environ is None else environ

        def get(name: str) -> str | None:
            value = env.get(PREFIX + name)
            return value.strip() if value and value.strip() else None

        max_bytes = get("BQ_MAX_BYTES_BILLED")
        try:
            max_bytes_value = int(max_bytes) if max_bytes else DEFAULT_MAX_BYTES_BILLED
        except ValueError as exc:
            raise SettingsError(f"{PREFIX}BQ_MAX_BYTES_BILLED must be an integer") from exc
        values: dict[str, object] = {
            "mode": get("MODE") or "cloud",
            "demo": _flag(get("DEMO")),
            "environment": get("ENV") or "dev",
            "catalog_root": Path(get("CATALOG_ROOT")) if get("CATALOG_ROOT") else None,
            "iap_audience": get("IAP_AUDIENCE"),
            "bq_project": get("BQ_PROJECT"),
            "bq_dataset": get("BQ_DATASET"),
            "bq_location": get("BQ_LOCATION"),
            "bq_max_bytes_billed": max_bytes_value,
            "static_dir": Path(get("STATIC_DIR")) if get("STATIC_DIR") else None,
        }
        values.update(overrides)
        settings = cls(**values)  # type: ignore[arg-type]
        if settings.local and env.get("K_SERVICE"):
            raise SettingsError("local mode refuses to run on Cloud Run (K_SERVICE is set): use IAP in cloud mode")
        return settings
