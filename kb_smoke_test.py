"""Teste rápido da base de conhecimento (file search) no Foundry.

Cria um documento de teste com um fato que o modelo não tem como saber, envia para um
vector store, cria um agente com file search e pergunta sobre esse fato. No fim, apaga tudo
que criou (agente, vector store e arquivo), mesmo que algo falhe no meio.

Uso:  python kb_smoke_test.py
Requer: az login e FOUNDRY_PROJECT_ENDPOINT / FOUNDRY_MODEL_DEPLOYMENT_NAME no .env.
"""
import os
import sys
import tempfile

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FileSearchTool, PromptAgentDefinition
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()
ENDPOINT = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
MODEL = os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME")
AGENT_NAME = "teste-kb-loggoes"
CODIGO = "ATRASO-7421"   # inventado: só dá para acertar lendo o documento
DOCUMENTO = f"""# Política de atrasos da LogGoes (documento de teste)

Quando uma entrega atrasa mais de 2 horas, o cliente recebe o código de cortesia {CODIGO},
que dá desconto na próxima entrega.
"""

if not ENDPOINT or not MODEL:
    sys.exit("Defina FOUNDRY_PROJECT_ENDPOINT e FOUNDRY_MODEL_DEPLOYMENT_NAME no .env")


def tentar(descricao, funcao):
    """Executa uma etapa de limpeza sem deixar uma falha esconder as outras."""
    try:
        funcao()
        print(f"  limpeza: {descricao} apagado")
    except Exception as e:
        print(f"  ⚠ limpeza: não consegui apagar {descricao}: {type(e).__name__}: {e}")
        print("    Apague manualmente no portal para não gerar custo.")


def citacoes(response):
    achadas = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for parte in getattr(item, "content", None) or []:
            for ann in getattr(parte, "annotations", None) or []:
                achadas.append(getattr(ann, "filename", None) or getattr(ann, "file_id", None) or "?")
    return achadas


project = AIProjectClient(endpoint=ENDPOINT, credential=DefaultAzureCredential())
openai = project.get_openai_client()
vector_store = agente = arquivo = None
ok = False
try:
    print("1/4 Criando o vector store...")
    try:  # expira sozinho em 1 dia, para não ficar gerando custo se a limpeza falhar
        vector_store = openai.vector_stores.create(
            name=AGENT_NAME, expires_after={"anchor": "last_active_at", "days": 1})
    except Exception as e:
        print(f"  (sem política de expiração: {type(e).__name__}); criando sem ela")
        vector_store = openai.vector_stores.create(name=AGENT_NAME)

    print("2/4 Enviando o documento de teste e aguardando a indexação...")
    with tempfile.TemporaryDirectory() as pasta:
        caminho = os.path.join(pasta, "politica_atrasos_teste.md")
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(DOCUMENTO)
        with open(caminho, "rb") as f:
            arquivo = openai.vector_stores.files.upload_and_poll(vector_store_id=vector_store.id, file=f)
    if getattr(arquivo, "status", "completed") != "completed":
        raise RuntimeError(f"A indexação terminou com status: {arquivo.status}")

    print("3/4 Criando o agente com file search...")
    agente = project.agents.create_version(
        agent_name=AGENT_NAME,
        definition=PromptAgentDefinition(
            model=MODEL,
            instructions="Responda em português. Use a busca em arquivos para responder e diga "
                         "claramente se a informação não estiver nos documentos.",
            tools=[FileSearchTool(vector_store_ids=[vector_store.id])],
        ),
    )

    print("4/4 Perguntando algo que só o documento responde...")
    conversa = openai.conversations.create()
    resposta = openai.responses.create(
        conversation=conversa.id,
        input="Qual é o código de cortesia dado quando uma entrega atrasa mais de 2 horas?",
        extra_body={"agent_reference": {"name": agente.name, "type": "agent_reference"}},
    )
    print(f"\nAgente: {resposta.output_text}")
    fontes = citacoes(resposta)
    print(f"Fontes citadas: {fontes if fontes else 'nenhuma'}")
    if "7421" in (resposta.output_text or ""):
        print("\n✔ A base de conhecimento funciona: a resposta veio do documento.")
        ok = True
    else:
        print("\n✖ A resposta NÃO trouxe o código do documento: o file search pode não estar sendo usado.")
except Exception as e:
    print(f"\n✖ Falhou: {type(e).__name__}: {e}")
    print("  403 costuma ser falta de função (Foundry User no projeto e, para enviar arquivos, "
          "Storage Blob Data Contributor na conta de armazenamento do projeto).")
finally:
    print("\nLimpando...")
    if agente:
        tentar("agente", lambda: project.agents.delete_version(agent_name=agente.name,
                                                               agent_version=agente.version))
    if vector_store:
        tentar("vector store", lambda: openai.vector_stores.delete(vector_store.id))
    if arquivo:
        tentar("arquivo", lambda: openai.files.delete(arquivo.id))
sys.exit(0 if ok else 1)
