# Copyright 2026 Google LLC
# Site-level initialization for the CX mortgage orchestrator
import os

# Ensure native gRPC DNS resolver
os.environ.setdefault("GRPC_DNS_RESOLVER", "native")



