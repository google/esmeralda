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

"""Cloud SQL-backed DatabaseTaskStore for A2A agent persistence."""

import os

from google.cloud.sql.connector import create_async_connector, IPTypes
from a2a.server.tasks import DatabaseTaskStore
import sqlalchemy.ext.asyncio

_connector = None


async def _get_cloud_sql_connection():
    global _connector
    if _connector is None:
        _connector = await create_async_connector()
    return await _connector.connect_async(
        os.environ["CLOUD_SQL_INSTANCE"],
        "asyncpg",
        user=os.environ["DB_IAM_USER"],
        db=os.environ.get("DB_NAME", "a2a_tasks"),
        enable_iam_auth=True,
        ip_type=IPTypes.PRIVATE,
    )


def build_cloud_sql_taskstore():
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "postgresql+asyncpg://",
        async_creator=_get_cloud_sql_connection,
        execution_options={"isolation_level": "AUTOCOMMIT"},
    )
    return DatabaseTaskStore(engine)
