"""Ferramentas (function calling) expostas ao agente e a execução delas.

Os argumentos vêm do modelo, então são tratados como entrada não confiável:
são validados antes de chamar o Fleetbase.

Escrita: o modelo NUNCA grava no Fleetbase. A ferramenta `propor_atribuicao` só valida e
guarda um rascunho; a gravação acontece em `confirmar_proposta`, chamada pela interface
quando o usuário clica em "Confirmar", usando os ids do rascunho (não o que o modelo disser).
"""
import time
import uuid

from azure.ai.projects.models import FunctionTool

import fleetbase_client as fb

STATUS_VALIDOS = ["todos", "created", "dispatched", "started", "completed", "canceled"]
MAX_ITENS = 30  # limite de itens devolvidos ao modelo (economiza tokens)


def _params(properties=None, required=None):
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


FERRAMENTAS = [
    FunctionTool(
        name="resumo_do_dia",
        description="Resumo geral da operação: total de pedidos, quantidade por status, "
                    "quantos estão atrasados e quantos motoristas estão livres.",
        parameters=_params(),
        strict=True,
    ),
    FunctionTool(
        name="listar_pedidos",
        description="Lista pedidos de entrega, opcionalmente filtrando por status. "
                    "Cada pedido traz código, rastreio, status, motorista, origem, destino, "
                    "horário agendado e se está atrasado.",
        parameters=_params(
            {"status": {"type": "string", "enum": STATUS_VALIDOS,
                        "description": "Status para filtrar. Use 'todos' para não filtrar."}},
            ["status"],
        ),
        strict=True,
    ),
    FunctionTool(
        name="listar_atrasados",
        description="Lista os pedidos atrasados: horário agendado já passou e o pedido "
                    "ainda não foi concluído nem cancelado.",
        parameters=_params(),
        strict=True,
    ),
    FunctionTool(
        name="consultar_pedido",
        description="Consulta um pedido específico. Aceita o código interno do pedido (o código curto "
                    "que aparece nas listas), o código de rastreio ou o id (order_...). "
                    "Maiúsculas, acentos e espaços não importam.",
        parameters=_params(
            {"identificador": {"type": "string",
                               "description": "Código interno, código de rastreio ou id do pedido."}},
            ["identificador"],
        ),
        strict=True,
    ),
    FunctionTool(
        name="listar_motoristas",
        description="Lista os motoristas com veículo, telefone e se estão ocupados "
                    "(têm pedido ativo) ou livres.",
        parameters=_params(
            {"apenas_livres": {"type": "boolean",
                               "description": "Se true, devolve só os motoristas livres."}},
            ["apenas_livres"],
        ),
        strict=True,
    ),
    FunctionTool(
        name="explicar_atraso",
        description="Explica o atraso de um pedido com base nos dados: quanto tempo está atrasado, se tem "
                    "motorista, quantos outros pedidos o motorista tem, a faixa da política de atrasos e uma "
                    "ação sugerida. Devolve OBSERVAÇÕES dos dados: o sistema não registra o motivo real do atraso.",
        parameters=_params(
            {"pedido": {"type": "string",
                        "description": "Código interno, código de rastreio ou id do pedido."}},
            ["pedido"],
        ),
        strict=True,
    ),
    FunctionTool(
        name="sugerir_motorista",
        description="Sugere, em ordem e com o motivo, quais motoristas poderiam receber um pedido que ainda "
                    "não tem motorista. NÃO atribui nada: para atribuir, use propor_atribuicao com o motorista "
                    "recomendado (o usuário confirma).",
        parameters=_params(
            {"pedido": {"type": "string",
                        "description": "Código interno, código de rastreio ou id do pedido."}},
            ["pedido"],
        ),
        strict=True,
    ),
    FunctionTool(
        name="propor_atribuicao",
        description="Prepara a atribuição de um motorista a um pedido existente. NÃO executa nada: "
                    "só valida e cria uma proposta que o usuário precisa confirmar na tela. "
                    "Só funciona para pedidos ativos que ainda não têm motorista.",
        parameters=_params(
            {"pedido": {"type": "string",
                        "description": "Código interno (ex.: LO050244), rastreio ou id do pedido."},
             "motorista": {"type": "string",
                           "description": "Nome (ou parte do nome) ou id do motorista."}},
            ["pedido", "motorista"],
        ),
        strict=True,
    ),
]

# ---------- propostas aguardando confirmação do usuário ----------
PROPOSTAS = {}      # proposta_id -> dados validados (ids reais do Fleetbase)
VALIDADE_S = 600    # a proposta expira em 10 minutos


def _limpar_expiradas():
    agora = time.time()
    for pid in [k for k, v in PROPOSTAS.items() if agora - v["criada_em"] > VALIDADE_S]:
        PROPOSTAS.pop(pid, None)


def obter_proposta(proposta_id):
    """Dados da proposta (para a interface mostrar) ou None se não existe/expirou."""
    _limpar_expiradas()
    return PROPOSTAS.get(proposta_id)


def cancelar_proposta(proposta_id):
    PROPOSTAS.pop(proposta_id, None)


def confirmar_proposta(proposta_id):
    """Executa a atribuição. Uso único: a proposta é removida antes de gravar."""
    _limpar_expiradas()
    prop = PROPOSTAS.pop(proposta_id, None)
    if not prop:
        return {"ok": False, "erro": "Proposta não encontrada ou expirada. Peça de novo ao agente."}
    try:
        r = fb.atribuir_motorista(prop["pedido_id"], prop["motorista_id"],
                                  prop["motorista_nome"], prop["despachar"])
        return {"ok": True, "pedido_codigo": prop["pedido_codigo"],
                "motorista_nome": prop["motorista_nome"], **r}
    except fb.FleetbaseError as e:
        return {"ok": False, "erro": str(e)}
    except Exception as e:
        return {"ok": False, "erro": f"Falha inesperada: {type(e).__name__}"}


def _propor(args):
    pedido, motorista = args.get("pedido"), args.get("motorista")
    if not isinstance(pedido, str) or not pedido.strip():
        return {"erro": "Informe o pedido."}
    if not isinstance(motorista, str) or not motorista.strip():
        return {"erro": "Informe o motorista."}
    r = fb.preparar_atribuicao(pedido[:60], motorista[:80])
    if "erro" in r:
        return r
    _limpar_expiradas()
    pid = uuid.uuid4().hex[:8]
    PROPOSTAS[pid] = {**r, "criada_em": time.time()}
    return {"proposta_id": pid, "status": "aguardando_confirmacao_do_usuario",
            "pedido": r["pedido_codigo"], "motorista": r["motorista_nome"], "avisos": r["avisos"],
            "instrucao": "NADA foi executado. Diga ao usuário que a proposta aguarda confirmação "
                         "nos botões da tela. Nunca diga que a atribuição foi feita."}


def _limitar(dados):
    if isinstance(dados, list) and len(dados) > MAX_ITENS:
        return {"itens": dados[:MAX_ITENS], "aviso": f"Mostrando {MAX_ITENS} de {len(dados)}."}
    return dados


def executar_ferramenta(nome, args):
    """Executa a ferramenta pedida pelo agente. Sempre devolve algo serializável,
    inclusive em caso de erro, para o agente poder explicar o problema ao usuário."""
    try:
        if nome == "resumo_do_dia":
            return fb.resumo_do_dia()
        if nome == "listar_pedidos":
            status = args.get("status", "todos")
            if status not in STATUS_VALIDOS:
                return {"erro": f"Status inválido: {status}. Válidos: {STATUS_VALIDOS}"}
            return _limitar(fb.listar_pedidos(None if status == "todos" else status))
        if nome == "listar_atrasados":
            return _limitar(fb.listar_atrasados())
        if nome == "consultar_pedido":
            ident = args.get("identificador")
            if not isinstance(ident, str) or not ident.strip():
                return {"erro": "Informe o código do pedido."}
            return fb.consultar_pedido(ident[:60])
        if nome in ("explicar_atraso", "sugerir_motorista"):
            pedido = args.get("pedido")
            if not isinstance(pedido, str) or not pedido.strip():
                return {"erro": "Informe o pedido."}
            funcao = fb.explicar_atraso if nome == "explicar_atraso" else fb.sugerir_motorista
            return funcao(pedido[:60])
        if nome == "propor_atribuicao":
            return _propor(args)
        if nome == "listar_motoristas":
            return _limitar(fb.listar_motoristas(bool(args.get("apenas_livres", False))))
        return {"erro": f"Ferramenta desconhecida: {nome}"}
    except fb.FleetbaseError as e:
        return {"erro": str(e)}
    except Exception as e:  # nunca deixar o loop do agente travar
        return {"erro": f"Falha inesperada em {nome}: {type(e).__name__}"}
