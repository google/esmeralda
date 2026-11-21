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

"""Read-only drift checks for settings applied outside Terraform.

The first source is the Agent Registry IAP policies written by ``grant_iap_egress.sh``. The per-entry
comparison reuses ``esmeralda.ops.registry_egress``, which reads the lists compiled from the catalog
in the Layer 4 and iap-egress unit outputs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from esmeralda.ops import registry_egress

SOURCE_IAP_EGRESS = "iap-egress"
SOURCES = (SOURCE_IAP_EGRESS,)


def check(root: Path, env: str) -> dict[str, Any]:
    """Compare script-managed settings with their compiled sources; never writes to the cloud."""
    result = registry_egress.check(root, env)
    checks = [
        {**entry, "source": SOURCE_IAP_EGRESS}
        for entry in result.get("checks") or []
        if not entry["ok"]
    ]
    return {
        "env": env,
        "passed": not checks,
        "sources": list(SOURCES),
        "ok_count": result.get("ok", 0),
        "failed": len(checks),
        "checks": checks,
        "seconds": result.get("seconds", 0),
    }
