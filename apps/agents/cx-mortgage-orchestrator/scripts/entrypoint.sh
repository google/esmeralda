#!/bin/bash
# Platform Trust Manager: installs the Agent Gateway root CA bundle at container start.
#
# The bundle (Agent Gateway TLS-inspection roots + internal *.esmeralda.internal Root CA) is
# env-specific, so it is NOT baked into the image: Terraform injects it at deploy time as the
# AGENT_GATEWAY_ROOT_CERTIFICATES env var (from the layer-4 governance output agw_root_ca_bundle).
# This keeps one image promotable from dev to prd by digest.
set -euo pipefail

if [ -n "${AGENT_GATEWAY_ROOT_CERTIFICATES:-}" ]; then
  python3 - <<'PY'
import os, re, subprocess

raw = os.environ["AGENT_GATEWAY_ROOT_CERTIFICATES"].replace("\\n", "\n")
certs = re.findall(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", raw, re.S)
if not certs:
    raise SystemExit("❌ AGENT_GATEWAY_ROOT_CERTIFICATES is set but contains no PEM certificate")

# 1. System trust store (OpenSSL, grpc, curl): SSL_CERT_FILE / REQUESTS_CA_BUNDLE /
#    GRPC_DEFAULT_SSL_ROOTS_FILE_PATH all point at /etc/ssl/certs/ca-certificates.crt.
for i, pem in enumerate(certs, 1):
    with open(f"/usr/local/share/ca-certificates/agw-{i}.crt", "w") as f:
        f.write(pem + "\n")
subprocess.run(["update-ca-certificates"], check=True, stdout=subprocess.DEVNULL)

# 2. certifi bundle (httpx/requests code paths that ignore SSL_CERT_FILE).
import certifi
path = certifi.where()
existing = open(path).read()
with open(path, "a") as f:
    for pem in certs:
        if pem not in existing:
            f.write("\n# Agent Gateway Root CA\n" + pem + "\n")

print(f"✅ Installed {len(certs)} Agent Gateway root certificate(s) from AGENT_GATEWAY_ROOT_CERTIFICATES")
PY
else
  echo "⚠️  AGENT_GATEWAY_ROOT_CERTIFICATES not set: egress through the Agent Gateway will fail TLS verification."
fi

exec "$@"
