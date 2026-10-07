"""Aplica a camada de resiliência a agent.py e atendimento.py.

O que muda:
  - timeout de 60 s e 3 tentativas do SDK no cliente do modelo (o padrão do SDK é 600 s);
  - falha da chamada ao modelo vira AgenteIndisponivel, com mensagem amigável por tipo de falha;
  - o limite de voltas do loop de ferramentas também levanta AgenteIndisponivel;
  - Despachante.perguntar e Atendente.perguntar devolvem a mensagem amigável. Se a falha deixou
    ferramentas já executadas (a conversa pode ficar com uma chamada de ferramenta sem resposta e
    travar), a PRÓXIMA mensagem daquela conversa abre uma conversa nova e a usa no lugar, sem as
    páginas precisarem saber (elas continuam passando o mesmo id).

Seguro: confere TODOS os trechos antes de gravar qualquer coisa, guarda a cópia do original em
~/antes_resiliencia/ (nunca sobrescreve uma cópia existente) e não faz nada se já foi aplicado.

    python aplicar_resiliencia.py
"""
import shutil
import sys
import time
from pathlib import Path

BLOCO_AGENT = r'''log = logging.getLogger("loggoes.agente")

TIMEOUT_S = 60.0      # sem resposta em 60 s, desiste (o padrão do SDK é 10 minutos)
TENTATIVAS_SDK = 3    # o SDK repete sozinho erros transitórios (429, 5xx, queda de conexão)

MENSAGENS_FALHA = {
    "autenticacao": "Não consegui acessar o Azure: a sessão pode ter expirado. "
                    "Rode 'az login' no terminal e tente de novo.",
    "limite": "O serviço de IA está com muitas requisições agora. Aguarde alguns segundos e tente de novo.",
    "tempo": "O serviço de IA demorou demais para responder. Tente de novo.",
    "conexao": "Não consegui falar com o serviço de IA (problema de conexão). "
               "Verifique a internet e tente de novo.",
    "servidor": "O serviço de IA está instável no momento. Tente de novo em instantes.",
    "outro": "Não consegui concluir agora por um erro inesperado. "
             "Tente de novo; se persistir, clique em Nova conversa.",
    "limite_de_voltas": "Não consegui concluir a consulta (limite de tentativas). "
                        "Tente reformular a pergunta.",
}


def classificar_falha(exc):
    """Motivo (chave de MENSAGENS_FALHA) de uma falha na chamada ao modelo. Não importa o pacote openai."""
    if exc is None:
        return "limite_de_voltas"
    nome = type(exc).__name__.lower()
    status = getattr(exc, "status_code", None)
    if status in (401, 403) or any(p in nome for p in ("authentication", "credential", "permission")):
        return "autenticacao"
    if status == 429 or "ratelimit" in nome:
        return "limite"
    if "timeout" in nome:
        return "tempo"
    if "connection" in nome:
        return "conexao"
    if isinstance(status, int) and status >= 500:
        return "servidor"
    return "outro"


class AgenteIndisponivel(Exception):
    """O agente não conseguiu concluir o turno.

    `mensagem`: texto seguro para mostrar ao usuário (nunca traz detalhes técnicos).
    `chamadas`: ferramentas que já tinham rodado antes da falha."""

    def __init__(self, causa=None, chamadas=None, motivo=None):
        self.causa = causa
        self.chamadas = chamadas or []
        self.motivo = motivo or classificar_falha(causa)
        self.mensagem = MENSAGENS_FALHA[self.motivo]
        super().__init__(self.motivo)
        if causa is not None:
            log.warning("Falha na chamada ao agente: %s (status %s)",
                        type(causa).__name__, getattr(causa, "status_code", None))


def cliente_resiliente(cliente):
    """O mesmo cliente do modelo, com timeout curto e tentativas do SDK. Sem with_options, devolve o original."""
    com_opcoes = getattr(cliente, "with_options", None)
    if not callable(com_opcoes):
        return cliente
    return com_opcoes(timeout=TIMEOUT_S, max_retries=TENTATIVAS_SDK)


def conversa_efetiva(bot, cid):
    """Id da conversa a usar na chamada ao modelo.

    A página guarda um id de conversa. Se uma falha deixou aquela conversa com uma chamada de
    ferramenta sem resposta (o que impede novas mensagens nela), a próxima mensagem abre uma
    conversa nova e passa a usá-la no lugar, sem a página precisar saber."""
    trocadas = bot.__dict__.setdefault("_conversas_trocadas", {})
    if cid in trocadas and trocadas[cid] is None:
        try:
            trocadas[cid] = bot.openai.conversations.create().id
        except Exception as e:
            raise AgenteIndisponivel(e) from e
    return trocadas.get(cid, cid)


def marcar_comprometida(bot, cid):
    """Marca a conversa `cid` (a que a página conhece) para ser substituída na próxima mensagem."""
    bot.__dict__.setdefault("_conversas_trocadas", {})[cid] = None'''


def juntar(*linhas):
    return "\n".join(linhas)


ALTERACOES = {
    "agent.py": [
        ("import json\nimport os\nimport sys\n",
         "import json\nimport logging\nimport os\nimport sys\n"),
        ("def fontes_da_resposta(resp):",
         BLOCO_AGENT + "\n\n\ndef fontes_da_resposta(resp):"),
        ("        resp = openai.responses.create(conversation=conversation_id, input=entrada)",
         juntar("        try:",
                "            resp = openai.responses.create(conversation=conversation_id, input=entrada)",
                "        except Exception as e:",
                "            raise AgenteIndisponivel(e, chamadas) from e")),
        (juntar('    return ("Não consegui concluir a consulta (limite de tentativas). "',
                '            "Tente reformular a pergunta."), chamadas'),
         '    raise AgenteIndisponivel(chamadas=chamadas, motivo="limite_de_voltas")'),
        (juntar("        self.openai = project.get_openai_client(agent_name=AGENT_NAME)",
                "        self.nova_conversa()"),
         juntar("        self.openai = cliente_resiliente(project.get_openai_client(agent_name=AGENT_NAME))",
                "        self.nova_conversa()")),
        ("        return executar_turno(self.openai, cid, texto, executar_ferramenta)",
         juntar("        try:",
                "            conversa = conversa_efetiva(self, cid)",
                "            return executar_turno(self.openai, conversa, texto, executar_ferramenta)",
                "        except AgenteIndisponivel as falha:",
                "            if falha.chamadas:   # ferramentas rodaram: a conversa pode ter ficado com uma chamada sem resposta",
                "                marcar_comprometida(self, cid)",
                "            return falha.mensagem, falha.chamadas")),
    ],
    "atendimento.py": [
        ("from agent import executar_turno",
         "from agent import (AgenteIndisponivel, cliente_resiliente, conversa_efetiva, executar_turno,\n"
         "                   marcar_comprometida)"),
        ("        self.openai = project.get_openai_client(agent_name=AGENT_NAME)",
         "        self.openai = cliente_resiliente(project.get_openai_client(agent_name=AGENT_NAME))"),
        (juntar("        resposta, chamadas = executar_turno(",
                "            self.openai, cid, texto,",
                "            lambda nome, args: executar_ferramenta_atendimento(nome, args, textos_cliente=historico),",
                "            fontes_out=fontes)",
                "        return resposta, chamadas, fontes"),
         juntar("        try:",
                "            conversa = conversa_efetiva(self, cid)",
                "            resposta, chamadas = executar_turno(",
                "                self.openai, conversa, texto,",
                "                lambda nome, args: executar_ferramenta_atendimento(nome, args, textos_cliente=historico),",
                "                fontes_out=fontes)",
                "        except AgenteIndisponivel as falha:",
                "            if falha.chamadas:   # ferramentas rodaram: a conversa pode ter ficado com uma chamada sem resposta",
                "                marcar_comprometida(self, cid)",
                "            return falha.mensagem, falha.chamadas, fontes",
                "        return resposta, chamadas, fontes")),
    ],
}

RESTAURAR = ("  cp ~/antes_resiliencia/agent.py ~/antes_resiliencia/atendimento.py .\n"
             "  python aplicar_resiliencia.py")


def main():
    novos = {}
    for arquivo, trocas in ALTERACOES.items():
        caminho = Path(arquivo)
        if not caminho.exists():
            sys.exit(f"ABORTADO: {arquivo} não encontrado. Rode dentro de ~/projeto-logistica.")
        texto = caminho.read_text(encoding="utf-8")
        if "conversa_efetiva" in texto:
            print(f"{arquivo}: já aplicado, nada a fazer.")
            continue
        if "AgenteIndisponivel" in texto:
            sys.exit(f"ABORTADO (nada foi alterado): {arquivo} tem a versão ANTERIOR deste patch.\n"
                     f"Restaure os originais e rode de novo:\n{RESTAURAR}")
        for antigo, novo in trocas:
            if texto.count(antigo) != 1:
                sys.exit(f"ABORTADO (nada foi alterado): em {arquivo} o trecho abaixo apareceu "
                         f"{texto.count(antigo)} vez(es), e o esperado era 1:\n\n{antigo}\n\n"
                         "O arquivo é diferente do que eu vi. Me mande o trecho em volta.")
            texto = texto.replace(antigo, novo)
        try:
            compile(texto, arquivo, "exec")
        except SyntaxError as e:
            sys.exit(f"ABORTADO (nada foi alterado): o resultado de {arquivo} teria erro de sintaxe: {e}")
        novos[arquivo] = texto

    if not novos:
        return
    pasta = Path.home() / "antes_resiliencia"
    pasta.mkdir(exist_ok=True)
    for arquivo, texto in novos.items():
        copia = pasta / arquivo
        if copia.exists():       # nunca sobrescreve uma cópia de segurança anterior
            copia = pasta / f"{arquivo}.{time.strftime('%Y%m%d_%H%M%S')}"
        shutil.copy(arquivo, copia)
        Path(arquivo).write_text(texto, encoding="utf-8")
        print(f"{arquivo}: alterado (cópia do original em {copia})")
    print("\nPronto. Rode: python test_resiliencia.py")


if __name__ == "__main__":
    main()
