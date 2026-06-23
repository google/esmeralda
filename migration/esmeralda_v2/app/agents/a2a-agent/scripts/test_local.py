# app/agents/a2a-agent/scripts/test_local.py
import asyncio
import os
import sys
import logging
import dotenv
import warnings

# Limpa alertas de APIs experimentais
warnings.filterwarnings("ignore", category=UserWarning)

# Carrega variáveis de teste offline
dotenv.load_dotenv()

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("test_local")

# Insere a pasta app no path de busca
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

try:
    from app.agents.a2a_agent.agent_app import adk_app
except ImportError as e:
    logger.error(f"Erro ao importar adk_app do agente: {e}")
    sys.exit(1)

async def run_local_test(query: str):
    logger.info("Iniciando setup e configurações do AdkApp local...")
    adk_app.set_up()
    
    # Simula identificadores de telemetria e faturamento (SoC)
    caller_context = {
      "tenant_id": "mortgage-bu",
      "project": "esmeralda-local-sandbox",
      "application_name": "mortgage-assistant-client"
    }

    logger.info(f"Enviando consulta local: '{query}'")
    print("\n--- EVENTOS TRANSMITIDOS (LOCAL STREAM) ---")

    try:
        # Invoca a execução offline por meio do interceptador de stream assíncrono
        async for event in adk_app.async_stream_query(
            message=query,
            user_id="dev-sandbox-user",
            caller_context=caller_context
        ):
            print(f"Evento Local: {event}")
            sys.stdout.flush()
            
    except Exception as e:
        logger.error(f"Erro ao simular execução local do agente: {e}")
    finally:
        print("-------------------------------------------\n")
        await adk_app.async_close()

if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "Gostaria de cotar taxas para refinanciamento, por favor."
    asyncio.run(run_local_test(query))
