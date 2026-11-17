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

"""Built-in catalog source adapters for live systems (read through snapshots).

Importing this package registers them (``esmeralda.catalog`` does it on import):

* ``project_labels`` (:mod:`.project_labels`): team attribution from project labels.

Each adapter is a :class:`esmeralda.catalog.snapshot.SnapshotSource`: ``esmeralda catalog fetch``
calls its ``fetch()`` (read-only, through :mod:`._live`), ``resolve`` reads only the snapshot.
"""

from esmeralda.catalog.adapters.project_labels import ProjectLabelsSource

__all__ = ["ProjectLabelsSource"]
