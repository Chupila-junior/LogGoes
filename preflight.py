"""Verificação pré-apresentação ("preflight"): diz se está tudo pronto, e o que fazer se não estiver.

Uso:
    python preflight.py            # verificação completa (uma chamada ao agente gasta poucos tokens)
    python preflight.py --rapido   # sem chamar o agente no Foundry (não gasta tokens)

Só LEITURA: não cria, altera nem apaga nada no Fleetbase nem no Azure.
Nunca imprime chaves nem o endereço completo do Foundry.
Saída: 0 se não houver falhas (avisos são permitidos); 1 se houver alguma falha.
"""
import importlib.metadata as md
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
import fleetbase_client as fb  # noqa: E402  (depois do load_dotenv)

OK, AVISO, FALHA, INFO = "✔", "⚠", "✖", "ℹ"
AQUI = Path(__file__).parent
VARIAVEIS = ["FLEETBASE_API_URL", "FLEETBASE_API_KEY", "FOUNDRY_PROJECT_ENDPOINT",
             "FOUNDRY_MODEL_DEPLOYMENT_NAME"]
ARQUIVOS = ["app.py", "pagina_despacho.py", "pagina_atendimento.py", "agent.py", "tools.py",
            "atendimento.py", "tools_atendimento.py", "fleetbase_client.py", "seed_fleetbase.py"]
DOCUMENTOS_ESPERADOS = 5
PACOTES_EXTRAS = ["pydeck"]      # usados pelo app, mesmo sem constarem no requirements.txt
LIMITE_ATRASADOS = 0.6           # acima disso, o mapa fica quase todo vermelho
MAX_PEDIDOS_SEED = 12            # o seed cria 10; bem mais que isso sugere pedidos duplicados
HORAS_MINIMAS_BASE = 48          # a base de conhecimento precisa durar até depois da apresentação
HORAS_BACKUP_VELHO = 24


class Relatorio:
    def __init__(self):
        self.itens = []

    def _limpar(self, texto):
        """Remove segredos de qualquer texto exibido."""
        texto = str(texto)
        for segredo in (fb.KEY, os.getenv("FOUNDRY_PROJECT_ENDPOINT")):
            if segredo and len(segredo) > 6:
                texto = texto.replace(segredo, "***")
        return texto

    def add(self, grupo, nivel, titulo, detalhe=""):
        self.itens.append((grupo, nivel, self._limpar(titulo), self._limpar(detalhe)))

    def contar(self, nivel):
        return sum(1 for _, n, _, _ in self.itens if n == nivel)


def versao_pacote(nome):
    try:
        return md.version(nome)
    except md.PackageNotFoundError:
        return None


def _nao_achou(e):
    texto = f"{type(e).__name__} {e}".lower()
    return "404" in texto or "not found" in texto or "notfound" in texto


# ---------- verificações ----------

def checar_ambiente(r):
    g = "Ambiente"
    faltam = [v for v in VARIAVEIS if not os.getenv(v)]
    if faltam:
        r.add(g, FALHA, "Variáveis do .env", "faltam: " + ", ".join(faltam) + ". Preencha o .env (veja .env.example).")
    else:
        r.add(g, OK, "Variáveis do .env", f"as {len(VARIAVEIS)} estão definidas")

    ignorados = (AQUI / ".gitignore").read_text(encoding="utf-8").splitlines() if (AQUI / ".gitignore").exists() else []
    if ".env" in [linha.strip() for linha in ignorados]:
        r.add(g, OK, ".env fora do Git", "o .gitignore protege o .env")
    else:
        r.add(g, AVISO, ".env fora do Git", "o .gitignore não lista o .env: adicione a linha '.env' antes de usar o Git")

    ausentes = [a for a in ARQUIVOS if not (AQUI / a).exists()]
    if ausentes:
        r.add(g, FALHA, "Arquivos do projeto", "faltam: " + ", ".join(ausentes))
    else:
        r.add(g, OK, "Arquivos do projeto", f"os {len(ARQUIVOS)} arquivos principais existem")
    docs = list((AQUI / "base_conhecimento").glob("*.md"))
    if len(docs) == DOCUMENTOS_ESPERADOS:
        r.add(g, OK, "Documentos da base", f"{len(docs)} arquivos em base_conhecimento/")
    else:
        r.add(g, AVISO, "Documentos da base", f"{len(docs)} arquivos em base_conhecimento/ (esperado: {DOCUMENTOS_ESPERADOS})")

    req = AQUI / "requirements.txt"
    pins = re.findall(r"^([A-Za-z0-9_.\-]+)==([\w.\-]+)", req.read_text(encoding="utf-8"), re.M) if req.exists() else []
    problemas = 0
    for nome, pino in pins:
        v = versao_pacote(nome)
        if v is None:
            problemas += 1
            r.add(g, FALHA, f"Pacote {nome}", "não instalado: ative o ambiente (source .venv/bin/activate) e rode pip install -r requirements.txt")
        elif v != pino:
            problemas += 1
            r.add(g, AVISO, f"Pacote {nome}", f"instalado {v}, requirements.txt pede {pino}")
    for nome in PACOTES_EXTRAS:
        if versao_pacote(nome) is None:
            problemas += 1
            r.add(g, FALHA, f"Pacote {nome}", "não instalado: pip install pydeck")
    if not problemas:
        r.add(g, OK, "Pacotes", f"{len(pins) + len(PACOTES_EXTRAS)} conferem com o requirements.txt")


def checar_fleetbase(r):
    """Devolve os dados do painel se o Fleetbase estiver utilizável; senão, None."""
    g = "Fleetbase"
    try:
        resp = requests.get(f"{fb.BASE}/drivers", params={"limit": 1}, timeout=5,
                            headers={"Authorization": f"Bearer {fb.KEY}"})
    except requests.RequestException:
        r.add(g, FALHA, "API", "não responde. Abra o Docker Desktop e confira com 'docker compose ps' na pasta do Fleetbase.")
        return None
    if resp.status_code == 503 or "fleetbase_not_configured" in resp.text:
        r.add(g, FALHA, "API", "o banco está sem as tabelas: rode 'docker compose exec application bash -c \"./deploy.sh\"'.")
        return None
    if resp.status_code in (401, 403):
        r.add(g, FALHA, "API", "chave de API recusada: gere outra em Developers > API Keys e atualize o .env.")
        return None
    if resp.status_code >= 400:
        r.add(g, FALHA, "API", f"respondeu HTTP {resp.status_code}")
        return None
    r.add(g, OK, "API e chave", "a API respondeu e a chave foi aceita")

    console = os.getenv("FLEETBASE_CONSOLE_URL", "http://localhost:4200")
    try:
        c = requests.get(console, timeout=5)
        if c.status_code < 500:
            r.add(g, OK, "Console", "respondeu")
        else:
            r.add(g, AVISO, "Console", f"respondeu HTTP {c.status_code}")
    except requests.RequestException:
        r.add(g, AVISO, "Console", f"não respondeu em {console} (você precisa dele para mostrar o pedido mudando)")

    try:
        dados = fb.painel()
    except fb.FleetbaseError as e:
        r.add(g, FALHA, "Dados dos pedidos", str(e))
        return None
    res, pedidos = dados["resumo"], dados["pedidos"]
    r.add(g, INFO, "Situação dos dados", f"{res['total_pedidos']} pedidos · {res['atrasados']} atrasados · "
          f"{res['sem_motorista']} sem motorista · {res['motoristas_livres']} motorista(s) livre(s) de {res['motoristas_total']}")

    refazer = "Apague os pedidos pelo console (Fleet-Ops) e rode 'python seed_fleetbase.py' pouco antes de apresentar."
    if res["total_pedidos"] == 0:
        r.add(g, FALHA, "Pedidos de teste", "não há pedidos. Rode 'python seed_fleetbase.py'.")
        return dados
    if res["total_pedidos"] > MAX_PEDIDOS_SEED:
        r.add(g, AVISO, "Quantidade de pedidos", f"{res['total_pedidos']} pedidos (o seed cria 10): possível duplicação. {refazer}")
    if res["motoristas_livres"] >= 1:
        r.add(g, OK, "Motorista livre", f"{res['motoristas_livres']} disponível para a demonstração de atribuição")
    else:
        r.add(g, AVISO, "Motorista livre", "nenhum motorista livre: a atribuição da demonstração vai aparecer com aviso de 'ocupado'. "
              "Conclua ou cancele um pedido no console, ou rode 'python seed_fleetbase.py --extra-driver'.")
    alvo = [p for p in pedidos if p["atrasado"] and p["ativo"] and not p["motorista"]]
    if alvo:
        r.add(g, OK, "Pedido atrasado sem motorista", f"{len(alvo)} (use o código {alvo[0]['codigo']} na demonstração)")
    else:
        r.add(g, AVISO, "Pedido atrasado sem motorista", f"não há nenhum: o passo 5 do roteiro não funcionará. {refazer}")
    ativos = [p for p in pedidos if p["ativo"]]
    if ativos and sum(1 for p in ativos if p["atrasado"]) / len(ativos) > LIMITE_ATRASADOS:
        r.add(g, AVISO, "Proporção de atrasados", "mais de 60% dos pedidos ativos estão atrasados (o mapa fica quase todo vermelho). "
              "Os horários do seed são relativos: " + refazer)
    codigos = [p for p in pedidos if p["rastreio"]]
    if codigos:
        r.add(g, OK, "Códigos de rastreio", f"{len(codigos)} pedidos têm código (a página de atendimento mostra exemplos)")
    else:
        r.add(g, AVISO, "Códigos de rastreio", "nenhum pedido tem código de rastreio: o atendimento não conseguirá consultar pedidos")
    return dados


def checar_azure(r, rapido):
    g = "Azure e Foundry"
    endpoint, modelo = os.getenv("FOUNDRY_PROJECT_ENDPOINT"), os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME")
    try:
        from azure.identity import DefaultAzureCredential
        cred = DefaultAzureCredential()
        token = cred.get_token("https://ai.azure.com/.default")
        minutos = int((token.expires_on - time.time()) / 60)
        r.add(g, OK, "Login do Azure", f"válido (o token é renovado sozinho; este vale por mais ~{max(minutos, 0)} min)")
    except ImportError:
        r.add(g, FALHA, "Login do Azure", "pacotes do Azure ausentes: ative o ambiente (source .venv/bin/activate).")
        return
    except Exception as e:
        r.add(g, FALHA, "Login do Azure", f"{type(e).__name__}. Refaça: az login --use-device-code")
        return
    if not endpoint or not modelo:
        r.add(g, INFO, "Foundry", "pulado: faltam variáveis do .env")
        return

    from azure.ai.projects import AIProjectClient
    import agent
    import atendimento
    try:
        project = AIProjectClient(endpoint=endpoint, credential=cred)
        openai = project.get_openai_client()
    except Exception as e:
        r.add(g, FALHA, "Projeto no Foundry", f"{type(e).__name__}: não consegui abrir o projeto. Confira FOUNDRY_PROJECT_ENDPOINT.")
        return

    if rapido:
        r.add(g, INFO, "Agente despachante", "não testado (--rapido)")
    else:
        try:
            cliente = project.get_openai_client(agent_name=agent.AGENT_NAME)
            conversa = cliente.conversations.create()
            resposta = cliente.responses.create(conversation=conversa.id, input="Responda apenas com a palavra: ok")
            if (getattr(resposta, "output_text", "") or "").strip():
                r.add(g, OK, "Agente despachante", f"'{agent.AGENT_NAME}' respondeu (o modelo e as permissões estão funcionando)")
            else:
                r.add(g, AVISO, "Agente despachante", "respondeu vazio: tente de novo")
        except Exception as e:
            if _nao_achou(e):
                r.add(g, AVISO, "Agente despachante", f"'{agent.AGENT_NAME}' ainda não existe. Ele é criado quando o app inicia (streamlit run app.py).")
            else:
                r.add(g, FALHA, "Agente despachante", f"{type(e).__name__}: {str(e)[:160]}")

    estado = atendimento._ler_estado()
    if not estado:
        r.add(g, AVISO, "Base de conhecimento", "não publicada (kb_state.json ausente): rode 'python atendimento.py --publicar-base'.")
        return
    try:
        vs = openai.vector_stores.retrieve(estado["vector_store_id"])
    except Exception as e:
        if _nao_achou(e):
            r.add(g, FALHA, "Base de conhecimento", "o vector store não existe mais (expirou?): rode 'python atendimento.py --publicar-base'.")
        else:
            r.add(g, AVISO, "Base de conhecimento", f"não consegui verificar ({type(e).__name__}). Teste na página de atendimento.")
        return
    status = getattr(vs, "status", None)
    expira = getattr(vs, "expires_at", None)
    prontos = getattr(getattr(vs, "file_counts", None), "completed", None)
    if status == "expired":
        r.add(g, FALHA, "Base de conhecimento", "expirou: rode 'python atendimento.py --publicar-base'.")
    elif status not in (None, "completed"):
        r.add(g, AVISO, "Base de conhecimento", f"status '{status}': aguarde a indexação terminar.")
    elif expira and (expira - time.time()) / 3600 < HORAS_MINIMAS_BASE:
        horas = max(int((expira - time.time()) / 3600), 0)
        r.add(g, AVISO, "Base de conhecimento", f"expira em ~{horas} h. Republique para renovar: 'python atendimento.py --publicar-base'.")
    elif prontos is not None and prontos != DOCUMENTOS_ESPERADOS:
        r.add(g, AVISO, "Base de conhecimento", f"{prontos} documentos indexados (esperado: {DOCUMENTOS_ESPERADOS}). Republique a base.")
    else:
        r.add(g, OK, "Base de conhecimento", "publicada, indexada e dentro da validade")


def checar_backup(r):
    g = "Backup"
    pasta = Path(os.getenv("BACKUP_DIR") or Path.home())
    arquivos = sorted(pasta.glob("backup_fleetbase_*.sql"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not arquivos:
        r.add(g, AVISO, "Backup do Fleetbase", f"nenhum backup_fleetbase_*.sql em {pasta}. Faça um (veja 'Backup do Fleetbase' no README).")
        return
    novo = arquivos[0]
    horas = (time.time() - novo.stat().st_mtime) / 3600
    with novo.open("rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(f.tell() - 2048, 0))
        completo = b"Dump completed" in f.read()
    if not completo:
        r.add(g, FALHA, "Backup do Fleetbase", f"{novo.name} está incompleto (falta '-- Dump completed' no final). Refaça o backup.")
    elif horas > HORAS_BACKUP_VELHO:
        r.add(g, AVISO, "Backup do Fleetbase", f"{novo.name} tem {int(horas)} h. Refaça depois do último ajuste nos dados.")
    else:
        r.add(g, OK, "Backup do Fleetbase", f"{novo.name} está completo e tem {int(horas)} h")
    r.add(g, INFO, "Restauração", "nunca foi testada: um backup que não foi restaurado ainda não é uma garantia.")


def checar_lembretes(r):
    g = "Lembretes (manuais)"
    r.add(g, INFO, "Custos", "confira o consumo em Cost Management no portal do Azure.")
    r.add(g, INFO, "Dados da demonstração", "os horários do seed são relativos: rode o seed pouco antes de apresentar.")
    r.add(g, INFO, "Depois do curso", "apague a base de conhecimento: python atendimento.py --apagar-base")


def executar(r, grupo, funcao, *args):
    """Roda uma verificação sem deixar que um erro inesperado derrube as outras."""
    try:
        return funcao(r, *args)
    except Exception as e:
        r.add(grupo, FALHA, f"Erro inesperado na verificação '{funcao.__name__}'", f"{type(e).__name__}: {str(e)[:160]}")
        return None


def imprimir(r):
    print(f"\nVerificação pré-apresentação · {datetime.now():%d/%m %H:%M}\n")
    grupo_atual = None
    for grupo, nivel, titulo, detalhe in r.itens:
        if grupo != grupo_atual:
            print(f"== {grupo} ==")
            grupo_atual = grupo
        print(f"  {nivel} {titulo}" + (f": {detalhe}" if detalhe else ""))
    falhas, avisos = r.contar(FALHA), r.contar(AVISO)
    print(f"\nResumo: {r.contar(OK)} ok · {avisos} aviso(s) · {falhas} falha(s)")
    if falhas:
        print("✖ NÃO ESTÁ PRONTO: resolva as falhas acima.")
    elif avisos:
        print("⚠ PRONTO COM RESSALVAS: leia os avisos.")
    else:
        print("✔ PRONTO PARA APRESENTAR.")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    r = Relatorio()
    executar(r, "Ambiente", checar_ambiente)
    executar(r, "Fleetbase", checar_fleetbase)
    executar(r, "Azure e Foundry", checar_azure, "--rapido" in argv)
    executar(r, "Backup", checar_backup)
    executar(r, "Lembretes (manuais)", checar_lembretes)
    imprimir(r)
    return 1 if r.contar(FALHA) else 0


if __name__ == "__main__":
    sys.exit(main())
