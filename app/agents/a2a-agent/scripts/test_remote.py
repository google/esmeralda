# app/agents/a2a-agent/scripts/test_remote.py
import argparse
import sys
import os
import logging
import requests
import json
import dotenv
import google.auth
import google.auth.transport.requests

dotenv.load_dotenv()

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("test_remote")

def get_gcp_access_token() -> str:
    """Carrega as credenciais ativas locais do desenvolvedor do Google Cloud."""
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        auth_request = google.auth.transport.requests.Request()
        credentials.refresh(auth_request)
        if credentials.token:
            logger.info("Token GCP obtido com sucesso via credenciais nativas.")
            return credentials.token
    except Exception as e:
        logger.warning(f"Incapaz de obter token de forma nativa: {e}. Executando fallback gcloud CLI...")
        
    import subprocess
    try:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"],
            text=True
        ).strip()
        logger.info("Token GCP obtido via fallback de gcloud CLI.")
        return token
    except Exception as ex:
        logger.error(f"Erro ao rodar fallback: {ex}")
        raise RuntimeError("Nenhuma credencial válida do Google Cloud encontrada.")

def main():
    parser = argparse.ArgumentParser(description="Dispara teste integrado contra o Vertex AI real.")
    parser.add_argument("--project", default=os.environ.get("GCP_PROJECT_ID", "prj-dev-esmeralda-agents"))
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--resource-id", default=os.environ.get("VERTEX_AGENT_RESOURCE_ID"))
    parser.add_argument("query", nargs="?", default="Gostaria de cotar taxas para refinanciamento, por favor.")
    args = parser.parse_args()

    if not args.resource_id:
        logger.error("Erro: A variável VERTEX_AGENT_RESOURCE_ID deve estar definida para resolver o agente.")
        sys.exit(1)

    # Constrói o endpoint SSE nativo da Vertex AI para streaming do ADK
    stream_url = f"https://{args.location}-aiplatform.googleapis.com/v1/projects/{args.project}/locations/{args.location}/reasoningEngines/{args.resource_id}:streamQuery?alt=sse"

    try:
        token = get_gcp_access_token()
    except Exception as e:
        logger.error(f"Erro de autenticação GCP: {e}")
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # Contextos injetados e encaminhados de forma limpa pelo trânsito de nuvem
    query_payload = {
        "class_method": "async_stream_query",
        "input": {
            "message": args.query,
            "user_id": "remote-verified-user",
            "caller_context": {
                "tenant_id": "mortgage-bu",
                "project": args.project,
                "application_name": "mortgage-assistant-client"
            }
        }
    }

    logger.info(f"Disparando stream HTTP POST contra {stream_url}...")
    print("\n--- STREAM REAL DE EVENTOS (VERTEX CLOUD) ---")

    try:
        response = requests.post(stream_url, json=query_payload, headers=headers, stream=True)
        response.raise_for_status()
        
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith("data:"):
                    data_str = decoded_line[5:].strip()
                    try:
                        # Tenta processar o JSON retornado do stream do Vertex AI
                        data_json = json.loads(data_str)
                        print(json.dumps(data_json, indent=2, ensure_ascii=False))
                    except json.JSONDecodeError:
                        print(data_str)
                    sys.stdout.flush()
    except Exception as e:
        logger.error(f"Falha de comunicação no tráfego de rede do stream: {e}")
    finally:
        print("---------------------------------------------\n")
        logger.info("Execução integrada do teste remoto finalizada.")

if __name__ == "__main__":
    main()
