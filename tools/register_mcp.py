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

import os
import json
import logging
import urllib.request
import urllib.error
import glob
import time
import subprocess

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_gcloud_token():
    try:
        token = subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode('utf-8').strip()
        return token
    except Exception as e:
        logger.error(f"Failed to get gcloud token: {e}")
        raise

def make_request(url, method="GET", data=None, token=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    
    if data:
        json_data = json.dumps(data).encode('utf-8')
    else:
        json_data = None

    req = urllib.request.Request(url, data=json_data, headers=headers, method=method)
    
    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 204:
                return None
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code == 409: # Already exists
            logger.info(f"Resource likely already exists (409): {url}")
            return {"status": "CONFLICT"}
        logger.error(f"HTTP Error {e.code} for {method} {url}: {e.read().decode('utf-8')}")
        raise

def discover_mcp_url(project_id, region, server_name):
    """Attempts to discover the Cloud Run URL for the given server."""
    try:
        cmd = [
            "gcloud",
            "run",
            "services",
            "describe",
            server_name,
            "--region",
            region,
            "--project",
            project_id,
            "--format",
            "value(status.url)",
        ]
        url = (
            subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
            .decode("utf-8")
            .strip()
        )
        return url
    except Exception:
        return None

def check_service_exists_agent_registry(project_id, region, server_name):
    """Checks if the service already exists in Agent Registry."""
    cmd = [
        "gcloud",
        "alpha",
        "agent-registry",
        "services",
        "describe",
        server_name,
        "--project",
        project_id,
        "--location",
        region,
    ]
    try:
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False

def register_agent_registry(project_id, region, server_name, tools, server_url):
    """Registers or updates the MCP server in Google Cloud Agent Registry (Alpha)."""
    formatted_tools = []
    for tool in tools:
        formatted_tools.append({
            "name": tool.get("name"),
            "description": tool.get("description", ""),
            "inputSchema": tool.get("inputSchema", {}),
        })
    spec_content = json.dumps({"tools": formatted_tools})
    
    spec_size = len(spec_content.encode("utf-8"))
    if spec_size > 10240:
        logger.error(f"Spec for {server_name} is too large for Agent Registry ({spec_size} bytes). Limit is 10KB.")
        return False

    if not server_url.endswith("/mcp"):
        server_url = f"{server_url.rstrip('/')}/mcp"

    exists = check_service_exists_agent_registry(project_id, region, server_name)

    if exists:
        logger.info(f"Updating existing service in Agent Registry: {server_name}")
        cmd = [
            "gcloud", "alpha", "agent-registry", "services", "update", server_name,
            "--project", project_id, "--location", region,
            f"--mcp-server-spec-content={spec_content}",
        ]
    else:
        logger.info(f"Creating new service in Agent Registry: {server_name}")
        cmd = [
            "gcloud", "alpha", "agent-registry", "services", "create", server_name,
            "--project", project_id, "--location", region,
            "--display-name", server_name,
            "--mcp-server-spec-type", "tool-spec",
            f"--mcp-server-spec-content={spec_content}",
            f"--interfaces=url={server_url},protocolBinding=JSONRPC",
        ]

    try:
        subprocess.check_call(cmd)
        logger.info(f"Successfully processed {server_name} in Agent Registry.")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to process {server_name} in Agent Registry: {e}")
        return False

def fetch_tools_from_mcp(mcp_url: str) -> list[dict]:
    """Calls tools/list on the MCP server using JSON-RPC."""
    if not mcp_url.endswith("/mcp"):
        mcp_url = f"{mcp_url.rstrip('/')}/mcp"

    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    logger.info(f"Fetching tools from MCP server at {mcp_url}...")
    
    # Gerar token OIDC para acesso a Cloud Run
    try:
        id_token = subprocess.check_output(["gcloud", "auth", "print-identity-token"]).decode('utf-8').strip()
    except Exception as e:
        logger.error(f"Failed to get gcloud identity token: {e}")
        id_token = None

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"
    }
    
    if id_token:
        headers["Authorization"] = f"Bearer {id_token}"

    req = urllib.request.Request(
        mcp_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as response:
            content = response.read().decode("utf-8")
            if "data:" in content:
                json_content = ""
                for line in content.splitlines():
                    if line.startswith("data:"):
                        json_content += line[len("data:"):].strip()
                content = json_content
            res_json = json.loads(content)
            return res_json.get("result", {}).get("tools", [])
    except Exception as e:
        logger.error(f"Failed to fetch tools from MCP: {e}")
        return []

def register_mcp_servers(project_id, region):
    token = get_gcloud_token()
    base_url = f"https://apihub.googleapis.com/v1/projects/{project_id}/locations/{region}"
    
    # Itera sobre todos os servidores em 'servers/'
    server_dirs = glob.glob("servers/*/")
    
    for tool_dir in server_dirs:
        server_name = os.path.basename(os.path.normpath(tool_dir))
        logger.info(f"Processando servidor: {server_name}...")
        
        # Descobrir URL do Cloud Run
        server_url = discover_mcp_url(project_id, region, server_name)
        if not server_url:
            logger.warning(f"URL do Cloud Run não encontrada para {server_name}. Verifique se o deploy foi feito com sucesso.")
            continue
            
        # Obter ferramentas dinamicamente da API (/mcp endpoint)
        tools = fetch_tools_from_mcp(server_url)
        if not tools:
            logger.warning(f"Nenhuma ferramenta encontrada na API de {server_name}.")
            continue
            
        api_id = server_name.replace("_", "-").lower() # Slug simples
        version_id = "version-1" # Deve ter entre 4-500 caracteres
        
        # 1. Registrar API
        logger.info(f"Registrando API: {api_id}")
        api_url = f"{base_url}/apis?api_id={api_id}"
        api_payload = {
            "display_name": server_name,
            "description": f"MCP Server: {server_name}",
            "documentation": {
                 "externalUri": "https://example.com" # Placeholder
            },
            "owner": {
                "displayName": "Agentic Foundation",
                "email": "agenticfoundation@agenticfoundation.com"
            },
            "api_style": {
                "enum_values": {
                    "values": [
                        {"id": "mcp-api"}
                    ]
                }
            }
        }
        try:
             # Tenta criar (POST) e captura 409 se já existir
             make_request(api_url, method="POST", data=api_payload, token=token)
        except urllib.error.HTTPError as e:
            if e.code == 409:
                logger.warning(f"API {api_id} já existe. Se não foi criada com estilo 'mcp-api', o registro de ferramentas falhará. Pode ser necessário deletá-la manualmente.")
            else:
                raise

        # 2. Registrar Versão
        logger.info(f"Registrando Versão: {version_id}")
        version_url = f"{base_url}/apis/{api_id}/versions?version_id={version_id}"
        version_payload = {
            "display_name": version_id,
            "documentation": {
                  "externalUri": "https://example.com" # Placeholder
            }
        }
        try:
            make_request(version_url, method="POST", data=version_payload, token=token)
        except urllib.error.HTTPError as e:
            if e.code != 409: raise

        # 3. Registrar Ferramentas (como Operações) no API Hub
        for tool in tools:
            tool_name = tool["name"]
            logger.info(f"Registrando Ferramenta no API Hub: {tool_name}")
            
            # O ID da operação deve ser único. Usamos o nome da ferramenta.
            op_url = f"{base_url}/apis/{api_id}/versions/{version_id}/operations?api_operation_id={tool_name}"
            
            # Preparar payload da ferramenta MCP
            
            input_schema = tool.get("inputSchema", {})
            if "jsonSchema" not in input_schema and input_schema:
                tool["inputSchema"] = {"jsonSchema": input_schema}
                
            output_schema = tool.get("outputSchema", {})
            if "jsonSchema" not in output_schema and output_schema:
                tool["outputSchema"] = {"jsonSchema": output_schema}
            
            op_payload = {
                "details": {
                    "description": tool.get("description", ""),
                    "mcpTool": tool
                }
            }
            
            try:
                make_request(op_url, method="POST", data=op_payload, token=token)
            except urllib.error.HTTPError as e:
                if e.code == 409:
                    logger.info(f"Ferramenta {tool_name} já existe no API Hub. Pulando atualização por enquanto.")
                else:
                    logger.error(f"Falha ao registrar ferramenta {tool_name} no API Hub: {e}")

        # 4. Registrar no Agent Registry
        logger.info(f"Registrando servidor no Agent Registry: {server_name}")
        register_agent_registry(project_id, region, server_name, tools, server_url)

if __name__ == "__main__":
    project_id = os.environ.get("PROJECT_ID")
    region = os.environ.get("REGION", "us-central1")
    
    if not project_id:
        print("❌ Erro: variável de ambiente PROJECT_ID não definida.")
        exit(1)
        
    print(f"🚀 Iniciando Registro MCP para o Projeto: {project_id} Região: {region}")
    register_mcp_servers(project_id, region)
    print("✅ Registro MCP Concluído.")
