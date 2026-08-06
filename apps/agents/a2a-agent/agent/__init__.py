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

try:
    import aiohttp
    _orig_aiohttp_init = aiohttp.ClientSession.__init__
    def _patched_aiohttp_init(self, *args, **kwargs):
        if "trust_env" not in kwargs:
            kwargs["trust_env"] = True
        _orig_aiohttp_init(self, *args, **kwargs)
    aiohttp.ClientSession.__init__ = _patched_aiohttp_init
except Exception:
    pass

USER_AUTH_TOKEN_KEY = "user_auth_token"

