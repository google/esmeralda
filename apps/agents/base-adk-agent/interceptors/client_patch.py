# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Client Patch Interceptor to monkeypatch google.genai.Client for global model endpoints and project defaulting."""

import os
import logging
from typing import Any

from interceptors.base import BaseInterceptor

logger = logging.getLogger(__name__)


class ClientPatchInterceptor(BaseInterceptor):
    """Monkeypatches google.genai.Client to respect MODEL_LOCATION and default projects."""

    def on_startup(self, app: Any) -> None:
        try:
            psc_gapis_ip = os.environ.get("GOOGLE_APIS_PSC_IP")

            if psc_gapis_ip:
                # 1. Socket resolution patch
                import socket
                _orig_getaddrinfo = socket.getaddrinfo

                def _custom_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
                    if family == 0 or family == socket.AF_UNSPEC:
                        family = socket.AF_INET
                    if isinstance(host, str) and (host.endswith(".googleapis.com") or host == "googleapis.com"):
                        host = psc_gapis_ip
                    return _orig_getaddrinfo(host, port, family, type, proto, flags)

                socket.getaddrinfo = _custom_getaddrinfo

                # 2. Asyncio resolution patch
                try:
                    import asyncio.base_events
                    _orig_asyncio_getaddrinfo = asyncio.base_events.BaseEventLoop.getaddrinfo

                    async def _custom_asyncio_getaddrinfo(self, host, port, *args, **kwargs):
                        if isinstance(host, str) and (host.endswith(".googleapis.com") or host == "googleapis.com"):
                            host = psc_gapis_ip
                        return await _orig_asyncio_getaddrinfo(self, host, port, *args, **kwargs)

                    asyncio.base_events.BaseEventLoop.getaddrinfo = _custom_asyncio_getaddrinfo
                except Exception:
                    pass

                # 3. aiohttp connector resolve_host patch
                try:
                    import aiohttp.connector
                    _orig_resolve_host = aiohttp.connector.TCPConnector._resolve_host

                    async def _custom_resolve_host(self, host, port, traces=None):
                        if isinstance(host, str) and (host.endswith(".googleapis.com") or host == "googleapis.com"):
                            return [
                                {
                                    "hostname": host,
                                    "host": psc_gapis_ip,
                                    "port": port,
                                    "family": socket.AF_INET,
                                    "proto": 6,
                                    "flags": 0,
                                }
                            ]
                        return await _orig_resolve_host(self, host, port, traces)

                    aiohttp.connector.TCPConnector._resolve_host = _custom_resolve_host
                except Exception:
                    pass

            # 4. google.genai.Client location & project defaulting (MODEL_LOCATION)
            from google.genai import Client as GenAIClient
            original_client_init = GenAIClient.__init__

            def patched_client_init(self, *args, **kwargs):
                model_loc = os.environ.get("MODEL_LOCATION", "global")
                if model_loc:
                    kwargs["location"] = model_loc
                elif "location" not in kwargs or kwargs["location"] is None:
                    kwargs["location"] = "global"
                if "project" not in kwargs or kwargs["project"] is None:
                    if "GOOGLE_CLOUD_PROJECT" in os.environ:
                        kwargs["project"] = os.environ["GOOGLE_CLOUD_PROJECT"]
                original_client_init(self, *args, **kwargs)

            GenAIClient.__init__ = patched_client_init

            # 5. httpx.AsyncClient CA bundle & Google API direct bypass
            try:
                import httpx
                import ssl
                import certifi

                combined_ca_path = "/tmp/combined_agw_ca.pem"
                if not os.path.exists(combined_ca_path):
                    with open(combined_ca_path, "w") as out_f:
                        if os.path.exists(certifi.where()):
                            out_f.write(open(certifi.where()).read() + "\n")
                        for extra_ca in ["/usr/local/share/ca-certificates/agw-gateway.crt", "/etc/ssl/certs/ca-certificates.crt"]:
                            if os.path.exists(extra_ca):
                                out_f.write(open(extra_ca).read() + "\n")

                os.environ["SSL_CERT_FILE"] = combined_ca_path
                os.environ["REQUESTS_CA_BUNDLE"] = combined_ca_path

                _orig_async_client_init = httpx.AsyncClient.__init__
                if not getattr(_orig_async_client_init, "_agw_patched", False):
                    def _patched_async_client_init(self, *args, **kwargs):
                        if "verify" not in kwargs or kwargs["verify"] is True:
                            kwargs["verify"] = ssl.create_default_context(cafile=combined_ca_path)
                        _orig_async_client_init(self, *args, **kwargs)
                    _patched_async_client_init._agw_patched = True
                    httpx.AsyncClient.__init__ = _patched_async_client_init
            except Exception as he:
                logger.warning(f"Failed to patch httpx.AsyncClient: {he}")

            logger.info("✅ Successfully applied PSC routing, MODEL_LOCATION=global, and httpx CA patches on startup.")
        except Exception as e:
            logger.error(f"Failed to patch clients in ClientPatchInterceptor: {e}")
