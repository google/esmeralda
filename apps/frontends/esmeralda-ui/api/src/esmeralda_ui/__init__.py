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

"""Read-only backend-for-frontend (BFF) of the Esmeralda console.

The browser talks only to this API. It reads the catalog through ``esmeralda.catalog`` (the
resolved catalog baked into the image), usage, chargeback, audit and traces from the existing
BigQuery views, and verifies the IAP identity of every request. It writes nothing and never emits
``esmeralda.usage`` records.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
