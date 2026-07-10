import os
import json
import logging
from typing import Dict, Any
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse, Response
import httpx
import google.auth
import google.auth.transport.requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ilb-routing-broker")

app = FastAPI(title="Esmeralda ILB Routing Broker", version="0.1.0")

# Load routing table from environment
AGENT_ENDPOINTS: Dict[str, Any] = {}
endpoints_env = os.getenv("AGENT_ENDPOINTS_JSON", "{}")
try:
    AGENT_ENDPOINTS = json.loads(endpoints_env)
    logger.info(f"Loaded {len(AGENT_ENDPOINTS)} agent routing configurations.")
except Exception as e:
    logger.error(f"Failed to parse AGENT_ENDPOINTS_JSON: {e}")

def get_google_auth_header(target_url: str = None) -> Dict[str, str]:
    """Fetches OIDC ID token or OAuth2 access token for internal VPC authentication."""
    try:
        credentials, _ = google.auth.default()
        auth_req = google.auth.transport.requests.Request()
        credentials.refresh(auth_req)
        return {"Authorization": f"Bearer {credentials.token}"}
    except Exception as e:
        logger.warning(f"Could not generate Google auth token: {e}")
        return {}

@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    return {"status": "healthy", "service": "routing-broker", "routes": list(AGENT_ENDPOINTS.keys())}

@app.api_route("/agent/{agent_name}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_agent_request(agent_name: str, path: str, request: Request):
    if agent_name not in AGENT_ENDPOINTS:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found in ILB routing table.")
    
    target_info = AGENT_ENDPOINTS[agent_name]
    target_base = target_info.get("endpoint_url", "").rstrip("/")
    if not target_base:
        raise HTTPException(status_code=500, detail=f"No target URL configured for agent '{agent_name}'.")

    target_url = f"{target_base}/{path}"
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.update(get_google_auth_header(target_url))

    body = await request.body()
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                params=request.query_params,
                content=body
            )
            return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))
        except httpx.RequestError as e:
            logger.error(f"Proxy request error to {target_url}: {e}")
            raise HTTPException(status_code=502, detail="Bad gateway communicating with upstream service.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080)
