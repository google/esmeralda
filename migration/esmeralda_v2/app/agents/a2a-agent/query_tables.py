# query_tables.py
import asyncio
import os
import sys
import google.auth
from google.cloud.sql.connector import create_async_connector, IPTypes
import sqlalchemy.ext.asyncio
from sqlalchemy import text

PROJECT_ID = "agent-ops-foundation-435f"
REGION = "us-central1"
CLOUD_SQL_INSTANCE = f"{PROJECT_ID}:{REGION}:a2a-agent-pg"
DB_NAME = "a2a_tasks"

os.environ["CLOUD_SQL_INSTANCE"] = CLOUD_SQL_INSTANCE
os.environ["DB_NAME"] = DB_NAME

print(f"[*] Cloud SQL Instance: {CLOUD_SQL_INSTANCE}")
print(f"[*] DB Name: {DB_NAME}")

_connector = None

async def _get_cloud_sql_connection():
    global _connector
    if _connector is None:
        _connector = await create_async_connector()
    return await _connector.connect_async(
        os.environ["CLOUD_SQL_INSTANCE"],
        "asyncpg",
        user="postgres",
        password="rkXLU79VpMdol0BvI8e1uThs",
        db=os.environ.get("DB_NAME", "a2a_tasks"),
        ip_type=IPTypes.PUBLIC,
    )

async def main():
    print("[*] Connecting to Cloud SQL PostgreSQL database as 'postgres'...")
    try:
        engine = sqlalchemy.ext.asyncio.create_async_engine(
            "postgresql+asyncpg://",
            async_creator=_get_cloud_sql_connection,
            execution_options={"isolation_level": "AUTOCOMMIT"},
        )
        
        async with engine.connect() as conn:
            print("[+] Connection established successfully!")
            
            # Query table names from information_schema
            query = text("SELECT table_name FROM information_schema.tables WHERE table_schema='public';")
            result = await conn.execute(query)
            tables = [row[0] for row in result.fetchall()]
            
            print(f"[+] Tables in 'public' schema: {tables}")
            
            # Let's also check if any records exist in 'tasks' or similar if they exist
            for table in tables:
                try:
                    count_query = text(f"SELECT COUNT(*) FROM {table};")
                    count_res = await conn.execute(count_query)
                    count = count_res.scalar()
                    print(f"  - Table '{table}': {count} rows")
                except Exception as ex:
                    print(f"  - Table '{table}': Could not count rows: {ex}")
                    
        await engine.dispose()
    except Exception as e:
        print(f"[-] Error connecting/querying: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
