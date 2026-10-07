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

USER_AUTH_TOKEN_KEY = "user_auth_token"

# Agent Gateway egress: the runtime routes traffic through an HTTP(S) forward proxy
# advertised via *_PROXY env vars. aiohttp silently ignores https:// proxies, so
# google-genai's aiohttp path (Gemini + Vertex sessions) would try a direct
# connection and fail with "Network is unreachable". Force google-genai onto
# httpx, which honors the proxy env like requests does.
import logging as _logging
import os as _os

_logging.getLogger(__name__).info(
    "Egress proxy env: %s",
    {k: v for k, v in _os.environ.items() if k.lower().endswith("_proxy")},
)
try:
    from google.genai import _api_client as _genai_api_client

    _genai_api_client.BaseApiClient._use_aiohttp = lambda self: False
except Exception as _e:  # pragma: no cover
    _logging.getLogger(__name__).warning("Could not disable google-genai aiohttp: %s", _e)

# Enable proxy trust in async clients (aiohttp) for Agent Gateway routing
try:
    import aiohttp
    _orig_aiohttp_init = aiohttp.ClientSession.__init__
    def _patched_aiohttp_init(self, *args, **kwargs):
        if "trust_env" not in kwargs:
            kwargs["trust_env"] = True
        _orig_aiohttp_init(self, *args, **kwargs)
    aiohttp.ClientSession.__init__ = _patched_aiohttp_init
except ImportError:
    pass
