"""Teste de mutação da trava do atendimento e do caminho de escrita.

Para cada grupo: planta defeitos, um por vez, no arquivo de código e confere se a suíte falha.
Os arquivos originais são sempre restaurados no final (inclusive com Ctrl+C).

    python mutacao_atendimento_escrita.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

GRUPOS = [
    ("tools_atendimento.py", "test_atendimento.py", [
        ("lista vazia deixa de recusar tudo", "if textos_cliente is not None:", "if textos_cliente:"),
        ("exige o código em TODAS as mensagens (all)",
         "if not any(alvo in texto for texto in textos_cliente):",
         "if not all(alvo in texto for texto in textos_cliente):"),
        ("busca o texto dentro do código (invertido)", "alvo in texto for", "texto in alvo for"),
        ("não normaliza o código antes de comparar", "alvo = fb.normalizar_codigo(codigo)", "alvo = codigo"),
        ("não trunca o código em 40", "fb.consultar_rastreio_publico(codigo[:40])", "fb.consultar_rastreio_publico(codigo)"),
        ("aceita código vazio", "if not isinstance(codigo, str) or not codigo.strip():", "if not isinstance(codigo, str):"),
        ("erro do Fleetbase vaza detalhes",
         'except fb.FleetbaseError:\n        return {"erro": "Não consegui consultar o pedido agora. Tente novamente em instantes."}',
         'except fb.FleetbaseError as e:\n        return {"erro": str(e)}'),
        ("exceção inesperada vaza a mensagem", 'f"Falha inesperada: {type(e).__name__}"',
         'f"Falha inesperada: {type(e).__name__}: {e}"'),
        ("ferramenta desconhecida consulta mesmo assim",
         'return {"erro": f"Ferramenta desconhecida: {nome}"}',
         'return fb.consultar_rastreio_publico(args.get("codigo"))'),
        ("esquema deixa de ser estrito", "strict=True,", "strict=False,"),
        ("esquema aceita campos extras", '"additionalProperties": False,', '"additionalProperties": True,'),
    ]),
    ("fleetbase_client.py", "test_escrita.py", [
        ("nome exato deixa de ter prioridade",
         'if q == _norm(d.get("id")) or q == _norm(d.get("name")):', 'if q == _norm(d.get("id")):'),
        ("basta UMA parte do nome (any)",
         'all(p in _norm(d.get("name")) for p in partes)', 'any(p in _norm(d.get("name")) for p in partes)'),
        ("motorista ambíguo escolhe o primeiro", "if len(achados) == 1:", "if len(achados) >= 1:"),
        ("preparar aceita pedido encerrado",
         "if not _is_active(o):\n        return {\"erro\": f\"O pedido {o.get('internal_id')} já está",
         "if False:\n        return {\"erro\": f\"O pedido {o.get('internal_id')} já está"),
        ("preparar aceita pedido que já tem motorista",
         "if isinstance(atual, dict):\n        return {\"erro\": f\"O pedido {o.get('internal_id')} já tem",
         "if False:\n        return {\"erro\": f\"O pedido {o.get('internal_id')} já tem"),
        ("ocupado conta pedidos encerrados",
         'if isinstance(dp, dict) and _is_active(p) and (dp.get("id") == d.get("id")',
         'if isinstance(dp, dict) and (dp.get("id") == d.get("id")'),
        ("ocupado ignora o nome", 'or dp.get("name") == d.get("name")):', "or False):"),
        ("aviso com contagem errada", "{len(ocupado_com)}", "{1}"),
        ("rascunho com id de pedido errado", '{"pedido_id": o["id"],', '{"pedido_id": d["id"],'),
        ("despachar sempre verdadeiro", '"despachar": (o.get("status") or "").lower() == "created",', '"despachar": True,'),
        ("grava o nome no lugar do id", '{"driver": motorista_id})', '{"driver": motorista_nome})'),
        ("não confere a releitura", "if not (isinstance(drv, dict) and", "if False and (isinstance(drv, dict) and"),
        ("aceita qualquer motorista na releitura", 'drv.get("id") == motorista_id', "True"),
        ("ignora o nome na releitura", "or (motorista_nome and drv.get(\"name\") == motorista_nome)", "or False"),
        ("despacha mesmo sem ser 'created'",
         'if despachar and (o.get("status") or "").lower() == "created":', "if despachar:"),
        ("ignora despachar=False", "if despachar and (o.get(\"status\")", "if True and (o.get(\"status\")"),
        ("não envia o despacho", '_send("PATCH", f"/orders/{pedido_id}/dispatch")', "None"),
        ("falha no despacho derruba tudo",
         'avisos.append(f"Motorista atribuído, mas o despacho falhou: {e}")', "raise"),
        ("resumo final usa o pedido antigo", "o = _pedido_por_id(pedido_id) or o", "o = o"),
        ("_pedido_por_id devolve o primeiro", 'if o.get("id") == pedido_id:', "if True:"),
    ]),
    ("fleetbase_client.py", "test_rastreio.py", [
        ("aceita código curto demais", '{8,24}$")', '{1,24}$")'),
        ("aceita código longo demais", '{8,24}$")', '{8,99}$")'),
        ("não valida o formato antes de buscar", "if not _RE_CODIGO.match(cod):", "if False:"),
        ("busca por pedaço do código (substring)",
         "if normalizar_codigo(guardado) == cod:", "if cod in normalizar_codigo(guardado):"),
        ("devolve o resumo interno inteiro", 'return {"rastreio": guardado,', 'return {**s, "rastreio": guardado,'),
        ("revela o nome do motorista", '"motorista_designado": bool(s["motorista"]),',
         '"motorista_designado": s["motorista"],'),
        ("hífen deixa de ser ignorado", 'r"[\\s\\-._]+"', 'r"[\\s._]+"'),
        ("status desconhecido vira 'indisponível'", 'STATUS_CLIENTE.get(status, o.get("status") or "indisponível")',
         'STATUS_CLIENTE.get(status, "indisponível")'),
        ("código consultado em minúsculas", '"codigo_consultado": cod.upper()}', '"codigo_consultado": cod}'),
        ("mensagem de não encontrado revela mais",
         '{"erro": "Não encontrei um pedido com esse código de rastreio.",',
         '{"erro": "Não encontrei um pedido com esse código de rastreio.", "interno": True,'),
    ]),
    ("atendimento.py", "test_historico.py", [
        ("trava desligada (textos_cliente=None)", "textos_cliente=historico", "textos_cliente=None"),
        ("histórico compartilhado entre conversas", "self._textos.setdefault(cid, [])",
         'self._textos.setdefault("global", [])'),
        ("histórico sem normalizar", "historico.append(fb.normalizar_codigo(texto))", "historico.append(texto)"),
        ("resposta do agente entra no histórico",
         "        return resposta, chamadas, fontes",
         "        historico.append(fb.normalizar_codigo(resposta))\n        return resposta, chamadas, fontes"),
        ("janela de 50 vira 5", "del historico[:-50]", "del historico[:-5]"),
        ("histórico sem limite", "del historico[:-50]", "pass"),
        ("ignora o id da conversa informado", "cid = conversation_id or self.conversation_id",
         "cid = self.conversation_id"),
        ("não descarta bytes inválidos", 'texto = texto.encode("utf-8", errors="ignore").decode("utf-8")',
         "texto = texto"),
    ]),
    ("agent.py", "test_resiliencia.py", [
        ("falha do modelo sobe como exceção crua", "raise AgenteIndisponivel(e, chamadas) from e", "raise"),
        ("limite de voltas com motivo errado", 'motivo="limite_de_voltas")', 'motivo="outro")'),
        ("timeout volta a ser enorme", "TIMEOUT_S = 60.0", "TIMEOUT_S = 600.0"),
        ("SDK sem tentativas", "TENTATIVAS_SDK = 3", "TENTATIVAS_SDK = 0"),
        ("cliente não recebe as opções", "return com_opcoes(timeout=TIMEOUT_S, max_retries=TENTATIVAS_SDK)",
         "return cliente"),
        ("status 401/403 deixa de ser autenticação", "if status in (401, 403) or", "if False or"),
        ("erro 500 deixa de ser 'servidor'", "status >= 500", "status > 500"),
        ("429 deixa de ser 'limite'", 'if status == 429 or "ratelimit" in nome:', "if False:"),
        ("mensagem vaza o detalhe técnico", "super().__init__(self.motivo)", "super().__init__(repr(causa))"),
        ("conversa substituta nunca é criada", "trocadas[cid] = bot.openai.conversations.create().id",
         "trocadas[cid] = cid"),
        ("sempre usa a conversa original", "return trocadas.get(cid, cid)", "return cid"),
        ("falha ao criar a substituta sobe crua", "raise AgenteIndisponivel(e) from e\n    return trocadas",
         "raise\n    return trocadas"),
        ("marcar não marca nada", 'bot.__dict__.setdefault("_conversas_trocadas", {})[cid] = None', "pass"),
        ("despachante deixa a falha subir", "except AgenteIndisponivel as falha:", "except KeyError as falha:"),
        ("despachante perde as chamadas feitas", "return falha.mensagem, falha.chamadas", "return falha.mensagem, []"),
        ("despachante troca a conversa mesmo sem ferramentas", "            if falha.chamadas:", "            if True:"),
        ("despachante nunca troca a conversa", "            if falha.chamadas:", "            if False:"),
        ("despachante ignora a conversa efetiva", "conversa = conversa_efetiva(self, cid)", "conversa = cid"),
        ("despachante manda a conversa original", "self.openai, conversa, texto,", "self.openai, cid, texto,"),
        ("despachante cliente sem opções",
         "cliente_resiliente(project.get_openai_client(agent_name=AGENT_NAME))",
         "project.get_openai_client(agent_name=AGENT_NAME)"),
    ]),
    ("atendimento.py", "test_resiliencia.py", [
        ("atendimento deixa a falha subir", "except AgenteIndisponivel as falha:", "except KeyError as falha:"),
        ("atendimento troca a conversa mesmo sem ferramentas", "            if falha.chamadas:", "            if True:"),
        ("atendimento nunca troca a conversa", "            if falha.chamadas:", "            if False:"),
        ("atendimento ignora a conversa efetiva", "conversa = conversa_efetiva(self, cid)", "conversa = cid"),
        ("atendimento manda a conversa original", "self.openai, conversa, texto,", "self.openai, cid, texto,"),
        ("atendimento perde as chamadas feitas", "return falha.mensagem, falha.chamadas, fontes",
         "return falha.mensagem, [], fontes"),
        ("atendimento cliente sem opções",
         "cliente_resiliente(project.get_openai_client(agent_name=AGENT_NAME))",
         "project.get_openai_client(agent_name=AGENT_NAME)"),
    ]),
    ("agent.py", "test_filtro.py", [
        ("filtro deixa de ser reconhecido pelo código", 'getattr(exc, "code", None) == "content_filter" or',
         'False or'),
        ("filtro deixa de ser reconhecido pelo texto", 'or "content_filter" in str(exc):', 'or False:'),
        ("filtro tratado como erro comum", 'return "filtro"   #', 'return "outro"   #'),
        ("qualquer 400 vira filtro", 'getattr(exc, "code", None) == "content_filter" or "content_filter" in str(exc):',
         'getattr(exc, "status_code", None) == 400:'),
        ("mensagem do filtro vaza o Azure", '"filtro": "Não posso processar essa mensagem. Pergunte sobre as entregas da LogGoes.",',
         '"filtro": "Bloqueado pelo Azure OpenAI content filter (https://go.microsoft.com).",'),
        ("filtro reconhecido depois da autenticação (401 passa na frente)",
         '    if getattr(exc, "code", None) == "content_filter" or "content_filter" in str(exc):\n        return "filtro"',
         '    if False:\n        return "filtro"'),
    ]),
]


def rodar(teste):
    # -B: não grava .pyc (mutações de mesmo tamanho no mesmo segundo reaproveitariam o .pyc anterior)
    r = subprocess.run([sys.executable, "-B", teste], capture_output=True, text=True)
    return r.returncode == 0, r.stderr


def grupo(codigo, teste, mutacoes):
    alvo = Path(codigo)
    if not alvo.exists() or not Path(teste).exists():
        print(f"\n[{codigo}] pulado: {codigo} ou {teste} não encontrado.")
        return
    original = alvo.read_text(encoding="utf-8")
    backup = Path(f".{codigo}.original.bak")
    shutil.copy(alvo, backup)
    print(f"\n=== {codigo} (suíte: {teste}) ===")
    try:
        ok, saida = rodar(teste)
        if not ok:
            print("A suíte já FALHA sem defeito plantado. Corrija isso antes:\n" + saida[-1500:])
            return
        detectados, inaplicaveis = 0, 0
        for nome, antigo, novo in mutacoes:
            if original.count(antigo) != 1:
                inaplicaveis += 1
                print(f"  ?  não aplicável (trecho não encontrado ou ambíguo): {nome}")
                continue
            alvo.write_text(original.replace(antigo, novo), encoding="utf-8")
            ok, _ = rodar(teste)
            if ok:
                print(f"  ✖  NÃO DETECTADO: {nome}")
            else:
                detectados += 1
                print(f"  ✔  detectado: {nome}")
        aplicadas = len(mutacoes) - inaplicaveis
        print(f"Resultado: {detectados}/{aplicadas} detectados"
              + (f" ({inaplicaveis} não aplicáveis)" if inaplicaveis else ""))
    finally:
        alvo.write_text(original, encoding="utf-8")
        backup.unlink(missing_ok=True)
        print(f"{codigo} restaurado.")


if __name__ == "__main__":
    for g in GRUPOS:
        grupo(*g)
