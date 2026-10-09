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

"""Agent Gateway trust: installs the gateway root CA bundle at container start.

The bundle (Agent Gateway TLS-inspection roots + internal *.esmeralda.internal Root CA) is
env-specific, so it is not baked into the image: Terraform injects it at deploy time as the
AGENT_GATEWAY_ROOT_CERTIFICATES env var (Layer 4 governance output agw_root_ca_bundle). This keeps
one image promotable from dev to prd by digest.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_VAR = "AGENT_GATEWAY_ROOT_CERTIFICATES"
SYSTEM_CA_DIR = Path("/usr/local/share/ca-certificates")
UPDATE_COMMAND = ("update-ca-certificates",)

_PEM = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.S)


def parse_bundle(raw: str) -> list[str]:
    """Returns the PEM certificates in ``raw`` (literal ``\\n`` sequences are accepted)."""
    return _PEM.findall(raw.replace("\\n", "\n"))


def install_gateway_ca(
    environ: Mapping[str, str] = os.environ,
    *,
    system_dir: Path = SYSTEM_CA_DIR,
    certifi_path: str | None = None,
    update_command: Sequence[str] = UPDATE_COMMAND,
) -> int:
    """Installs the gateway root CAs into the system trust store and the certifi bundle.

    The system store covers OpenSSL, gRPC and curl (SSL_CERT_FILE, REQUESTS_CA_BUNDLE and
    GRPC_DEFAULT_SSL_ROOTS_FILE_PATH point at it in the image); certifi covers httpx/requests code
    paths that ignore SSL_CERT_FILE. Safe to run more than once.

    Returns the number of certificates found (0 when the env var is not set).
    Raises ValueError if the env var is set but holds no PEM certificate.
    """
    raw = environ.get(ENV_VAR, "")
    if not raw.strip():
        logger.warning("%s not set: egress through the Agent Gateway will fail TLS verification.", ENV_VAR)
        return 0

    certs = parse_bundle(raw)
    if not certs:
        raise ValueError(f"{ENV_VAR} is set but contains no PEM certificate")

    if system_dir.is_dir() and shutil.which(update_command[0]):
        for i, pem in enumerate(certs, 1):
            (system_dir / f"agw-{i}.crt").write_text(pem + "\n")
        subprocess.run(list(update_command), check=True, stdout=subprocess.DEVNULL)
    else:
        logger.warning("System trust store not available (%s); installed into certifi only.", system_dir)

    if certifi_path is None:
        import certifi

        certifi_path = certifi.where()
    bundle = Path(certifi_path)
    existing = bundle.read_text()
    missing = [pem for pem in certs if pem not in existing]
    if missing:
        with bundle.open("a") as f:
            for pem in missing:
                f.write("\n# Agent Gateway Root CA\n" + pem + "\n")

    return len(certs)
