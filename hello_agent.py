"""Hello world: cria o agente no Foundry e troca duas mensagens com ele.

Baseado no quickstart atual do SDK (azure-ai-projects 2.x).
Requer: az login feito e as variáveis abaixo no .env:
    FOUNDRY_PROJECT_ENDPOINT=https://<recurso>.services.ai.azure.com/api/projects/<projeto>
    FOUNDRY_MODEL_DEPLOYMENT_NAME=<nome do deployment>
"""
import os
import sys

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()
ENDPOINT = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
MODEL = os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME")
AGENT_NAME = "despachante-logistico"

if not ENDPOINT or not MODEL:
    sys.exit("Defina FOUNDRY_PROJECT_ENDPOINT e FOUNDRY_MODEL_DEPLOYMENT_NAME no .env")

project = AIProjectClient(endpoint=ENDPOINT, credential=DefaultAzureCredential())

# Cria o agente (ou uma nova versão dele, se já existir)
agent = project.agents.create_version(
    agent_name=AGENT_NAME,
    definition=PromptAgentDefinition(
        model=MODEL,
        instructions=(
            "Você é um assistente de despacho logístico. "
            "Responda sempre em português do Brasil, de forma curta e objetiva."
        ),
    ),
)
print(f"Agente criado: {agent.name} (versão {agent.version})")

# Cliente já ligado ao agente + uma conversa para manter o histórico
openai = project.get_openai_client(agent_name=AGENT_NAME)
conversation = openai.conversations.create()

for pergunta in ["Olá! Quem é você e o que você consegue fazer?",
                 "Resuma isso em uma frase."]:
    response = openai.responses.create(conversation=conversation.id, input=pergunta)
    print(f"\nVocê: {pergunta}\nAgente: {response.output_text}")
