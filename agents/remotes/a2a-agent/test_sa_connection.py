# test_sa_connection.py
import asyncio
import os
import sys
import google.auth
from google.auth import impersonated_credentials
from google.cloud.sql.connector import create_async_connector, IPTypes
import sqlalchemy.ext.asyncio
from sqlalchemy import text

PROJECT_ID = "agent-ops-foundation-435f"
REGION = "us-central1"
CLOUD_SQL_INSTANCE = f"{PROJECT_ID}:{REGION}:a2a-agent-pg"
DB_NAME = "a2a_tasks"
SA_EMAIL = f"test-vm-sa@{PROJECT_ID}.iam.gserviceaccount.com"
DB_USER = f"test-vm-sa@{PROJECT_ID}.iam"

os.environ["CLOUD_SQL_INSTANCE"] = CLOUD_SQL_INSTANCE
os.environ["DB_NAME"] = DB_NAME

print(f"[*] Target Cloud SQL Instance: {CLOUD_SQL_INSTANCE}")
print(f"[*] Target Service Account: {SA_EMAIL}")
print(f"[*] PostgreSQL DB User role: {DB_USER}")

_connector = None

async def _get_cloud_sql_connection():
    global _connector
    if _connector is None:
        # 1. Load the developer source credentials
        source_credentials, project = google.auth.default()
        
        # 2. Explicitly create the impersonated service account credentials
        target_credentials = impersonated_credentials.Credentials(
            source_credentials=source_credentials,
            target_principal=SA_EMAIL,
            target_scopes=[
                "https://www.googleapis.com/auth/sqlservice.admin",
                "https://www.googleapis.com/auth/cloud-platform"
            ],
        )
        
        # 3. Create the connector using the target service account credentials
        _connector = await create_async_connector(credentials=target_credentials)
        
    return await _connector.connect_async(
        os.environ["CLOUD_SQL_INSTANCE"],
        "asyncpg",
        user=DB_USER,
        db=os.environ.get("DB_NAME", "a2a_tasks"),
        enable_iam_auth=True,
        ip_type=IPTypes.PUBLIC,  # Using public IP for testing from current environment
    )

async def main():
    print("[*] Establishing IAM authenticated connection to Cloud SQL...")
    try:
        engine = sqlalchemy.ext.asyncio.create_async_engine(
            "postgresql+asyncpg://",
            async_creator=_get_cloud_sql_connection,
            execution_options={"isolation_level": "AUTOCOMMIT"},
        )
        
        async with engine.connect() as conn:
            print("[+] Connection established successfully!")
            
            # Query who we are logged in as inside PostgreSQL
            identity_query = text("SELECT CURRENT_USER, CURRENT_DATABASE();")
            identity_res = await conn.execute(identity_query)
            current_user, current_db = identity_res.fetchone()
            print(f"[+] PostgreSQL Identity: logged in as user '{current_user}' inside database '{current_db}'")
            
            # Query table names from public schema
            query = text("SELECT table_name FROM information_schema.tables WHERE table_schema='public';")
            result = await conn.execute(query)
            tables = [row[0] for row in result.fetchall()]
            print(f"[+] Visible tables in 'public' schema: {tables}")
            
        await engine.dispose()
        print("[+] Test completed successfully!")
    except Exception as e:
        print(f"[-] Error connecting/querying: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
