"""Cliente do Fleetbase: devolve dados enxutos e prontos para o agente.

Regras de negócio definidas aqui (o Fleetbase não as fornece):
- "atrasado": pedido não concluído/cancelado com horário agendado já passado.
- "motorista ocupado": tem ao menos um pedido ativo atribuído a ele
  (o campo status/online do motorista não reflete isso).
"""
import os
import re
import unicodedata
from math import asin, cos, radians, sin, sqrt
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()
BASE = os.getenv("FLEETBASE_API_URL", "http://localhost:8000/v1").rstrip("/")
KEY = os.getenv("FLEETBASE_API_KEY")
TZ = ZoneInfo("America/Sao_Paulo")
FINISHED = {"completed", "canceled", "cancelled"}


class FleetbaseError(Exception):
    pass


def _get(path, params=None):
    try:
        r = requests.get(f"{BASE}{path}", params=params, timeout=30,
                         headers={"Authorization": f"Bearer {KEY}"})
    except requests.RequestException as e:
        raise FleetbaseError(f"Não consegui falar com o Fleetbase: {e}")
    if r.status_code >= 400:
        raise FleetbaseError(f"Fleetbase respondeu HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def _send(method, path, body=None):
    """Chamada de escrita (PUT/PATCH). Levanta FleetbaseError com o motivo em caso de falha."""
    try:
        r = requests.request(method, f"{BASE}{path}", json=body, timeout=30,
                             headers={"Authorization": f"Bearer {KEY}",
                                      "Content-Type": "application/json"})
    except requests.RequestException as e:
        raise FleetbaseError(f"Não consegui falar com o Fleetbase: {e}")
    if r.status_code >= 400:
        raise FleetbaseError(f"Fleetbase respondeu HTTP {r.status_code}: {r.text[:200]}")
    return r.json() if r.text else {}


def _norm(texto):
    """Minúsculas e sem acentos, para comparar nomes."""
    sem = "".join(c for c in unicodedata.normalize("NFD", texto or "")
                  if unicodedata.category(c) != "Mn")
    return sem.casefold().strip()


def _items(resp):
    return resp.get("data", []) if isinstance(resp, dict) else (resp or [])


def _parse_dt(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _local(dt):
    return dt.astimezone(TZ).strftime("%d/%m %H:%M") if dt else None


def _place(p):
    if not isinstance(p, dict):
        return None
    return {"nome": p.get("name"), "endereco": p.get("address"), "bairro": p.get("neighborhood")}


def _tracking_code(order):
    t = order.get("tracking_number")
    return t.get("tracking_number") if isinstance(t, dict) else t


def _is_active(order):
    return (order.get("status") or "").lower() not in FINISHED


def _is_late(order, now=None):
    now = now or datetime.now(timezone.utc)
    sched = _parse_dt(order.get("scheduled_at"))
    return bool(sched and sched < now and _is_active(order))


def _summarize_order(o, now=None):
    driver = o.get("driver_assigned")
    payload = o.get("payload") or {}
    return {
        "codigo": o.get("internal_id"),
        "id": o.get("id"),
        "rastreio": _tracking_code(o),
        "status": o.get("status"),
        "motorista": driver.get("name") if isinstance(driver, dict) else None,
        "origem": _place(payload.get("pickup")),
        "destino": _place(payload.get("dropoff")),
        "agendado_para": _local(_parse_dt(o.get("scheduled_at"))),
        "atrasado": _is_late(o, now),
        "observacoes": o.get("notes"),
    }


def _all_orders():
    return _items(_get("/orders", {"limit": 100}))


# ---------- funções expostas ao agente ----------

def listar_pedidos(status=None, apenas_atrasados=False):
    """Lista pedidos. status: created, dispatched, started, completed, canceled."""
    now = datetime.now(timezone.utc)
    result = []
    for o in _all_orders():
        if status and (o.get("status") or "").lower() != status.lower():
            continue
        if apenas_atrasados and not _is_late(o, now):
            continue
        result.append(_summarize_order(o, now))
    return result


def listar_atrasados():
    return listar_pedidos(apenas_atrasados=True)


def _achar_pedido(pedidos, identificador):
    """Acha por código interno, id ou código de rastreio, ignorando caixa, acentos, espaços e hífens."""
    ident = normalizar_codigo(identificador)
    if not ident:
        return None
    for o in pedidos:
        candidatos = {normalizar_codigo(o.get("internal_id")), normalizar_codigo(o.get("id")),
                      normalizar_codigo(_tracking_code(o))}
        candidatos.discard("")
        if ident in candidatos:
            return o
    return None


def consultar_pedido(identificador):
    """Busca por código interno, id (order_...) ou código de rastreio."""
    o = _achar_pedido(_all_orders(), identificador)
    if o:
        return _summarize_order(o)
    return {"erro": f"Pedido '{identificador}' não encontrado."}


STATUS_CLIENTE = {"created": "criado (aguardando a designação de um motorista)",
                  "dispatched": "despachado (já tem motorista designado)",
                  "started": "iniciado (em rota de entrega)",
                  "completed": "concluído (entregue)", "canceled": "cancelado"}
_RE_CODIGO = re.compile(r"^[a-z0-9]{8,24}$")


def normalizar_codigo(texto):
    """Forma comparável de um código: sem acentos, caixa, espaços, hífens, pontos e sublinhados.
    Os códigos de rastreio gerados pelo Fleetbase podem ter letras acentuadas (ex.: 'Gó8881740690BR')."""
    return re.sub(r"[\s\-._]+", "", _norm(texto))


def consultar_rastreio_publico(codigo):
    """Consulta para o CLIENTE FINAL. Privilégio mínimo:
    - só aceita o código de rastreio (não o código interno LO... nem o id order_...);
    - a comparação ignora caixa, acentos, espaços e hífens;
    - devolve apenas situação, horário agendado, atraso e o bairro do destino:
      nunca motorista, telefone, origem, ids internos ou observações;
    - se não achar, a mensagem é genérica (não confirma se o código existe) e mostra qual
      código foi consultado, para o cliente perceber uma digitação trocada.
    """
    cod = normalizar_codigo(codigo)
    if not _RE_CODIGO.match(cod):
        return {"erro": "Código de rastreio inválido. Confira o código informado na confirmação do pedido."}
    for o in _all_orders():
        guardado = str(_tracking_code(o) or "")
        if normalizar_codigo(guardado) == cod:
            s = _summarize_order(o)
            status = (o.get("status") or "").lower()
            return {"rastreio": guardado,
                    "situacao": STATUS_CLIENTE.get(status, o.get("status") or "indisponível"),
                    "agendado_para": s["agendado_para"],
                    "atrasado": s["atrasado"],
                    "motorista_designado": bool(s["motorista"]),
                    "bairro_destino": (s["destino"] or {}).get("bairro")}
    return {"erro": "Não encontrei um pedido com esse código de rastreio.",
            "codigo_consultado": cod.upper()}


def _montar_motoristas(drivers, orders):
    """Motoristas com disponibilidade calculada pelos pedidos ativos de cada um."""
    busy = {}
    for o in orders:
        d = o.get("driver_assigned")
        if isinstance(d, dict) and _is_active(o):
            busy.setdefault(d.get("id") or d.get("name"), []).append(o.get("internal_id"))
    result = []
    for d in drivers:
        pedidos = busy.get(d.get("id")) or busy.get(d.get("name")) or []
        v = d.get("vehicle") if isinstance(d.get("vehicle"), dict) else {}
        result.append({
            "id": d.get("id"),
            "nome": d.get("name"),
            "telefone": d.get("phone"),
            "veiculo": " ".join(x for x in [v.get("make"), v.get("model")] if x) or None,
            "placa": v.get("plate_number"),
            "ocupado": bool(pedidos),
            "pedidos_ativos": pedidos,
        })
    return result


def listar_motoristas(apenas_livres=False):
    """Lista motoristas com disponibilidade calculada pelos pedidos ativos."""
    motoristas = _montar_motoristas(_items(_get("/drivers", {"limit": 100})), _all_orders())
    return [m for m in motoristas if not (apenas_livres and m["ocupado"])]


def resumo_do_dia():
    pedidos = _all_orders()
    por_status = {}
    for o in pedidos:
        s = o.get("status") or "desconhecido"
        por_status[s] = por_status.get(s, 0) + 1
    now = datetime.now(timezone.utc)
    motoristas = listar_motoristas()
    return {
        "total_pedidos": len(pedidos),
        "por_status": por_status,
        "atrasados": sum(1 for o in pedidos if _is_late(o, now)),
        "motoristas_total": len(motoristas),
        "motoristas_livres": sum(1 for m in motoristas if not m["ocupado"]),
    }


def _xy(place):
    """[longitude, latitude] de um local, ou None se não houver posição válida (0,0 = sem posição)."""
    loc = (place or {}).get("location") or {}
    c = loc.get("coordinates")
    if isinstance(c, (list, tuple)) and len(c) == 2 and all(isinstance(v, (int, float)) for v in c):
        if not (c[0] == 0 and c[1] == 0):
            return [c[0], c[1]]
    return None


def painel():
    """Dados para a tela: pedidos (com coordenadas), motoristas e totais. Faz só 2 chamadas à API."""
    orders = _all_orders()
    drivers = _items(_get("/drivers", {"limit": 100}))
    now = datetime.now(timezone.utc)
    pedidos = []
    for o in orders:
        item = _summarize_order(o, now)
        payload = o.get("payload") or {}
        item.update({"ativo": _is_active(o), "agendado_iso": o.get("scheduled_at") or "",
                     "origem_xy": _xy(payload.get("pickup")), "destino_xy": _xy(payload.get("dropoff"))})
        pedidos.append(item)
    pedidos.sort(key=lambda p: (not p["atrasado"], p["agendado_iso"]))   # atrasados primeiro
    motoristas = _montar_motoristas(drivers, orders)
    por_status = {}
    for o in orders:
        st_ = o.get("status") or "desconhecido"
        por_status[st_] = por_status.get(st_, 0) + 1
    resumo = {
        "total_pedidos": len(pedidos),
        "por_status": por_status,
        "atrasados": sum(1 for p in pedidos if p["atrasado"]),
        "sem_motorista": sum(1 for p in pedidos if p["ativo"] and not p["motorista"]),
        "motoristas_total": len(motoristas),
        "motoristas_livres": sum(1 for m in motoristas if not m["ocupado"]),
    }
    return {"pedidos": pedidos, "motoristas": motoristas, "resumo": resumo}


# ---------- análises para o despachante (somente leitura) ----------

# Faixas da política de atrasos. Devem refletir base_conhecimento/02_politica_de_atrasos.md
# (um teste confere a correspondência). Limites em minutos; None = sem limite.
FAIXAS_ATRASO = [
    (30, "até 30 minutos: tolerância operacional, sem compensação"),
    (120, "de 30 minutos a 2 horas: pedir desculpas e priorizar o atendimento"),
    (1440, "de 2 a 24 horas: o cliente tem direito a um cupom de 10% na próxima entrega"),
    (None, "mais de 24 horas: reembolso integral do frete"),
]


def faixa_de_atraso(minutos):
    for limite, texto in FAIXAS_ATRASO:
        if limite is None or minutos <= limite:
            return texto


def _duracao(minutos):
    horas, resto = divmod(int(minutos), 60)
    return f"{horas} h {resto:02d} min" if horas else f"{resto} min"


def _haversine_km(a, b):
    """Distância em km entre dois pontos [longitude, latitude]."""
    lon1, lat1, lon2, lat2 = map(radians, (a[0], a[1], b[0], b[1]))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * asin(sqrt(h))


def _ativos_por_motorista(pedidos):
    ativos = {}
    for o in pedidos:
        d = o.get("driver_assigned")
        if isinstance(d, dict) and _is_active(o):
            ativos.setdefault(d.get("id") or d.get("name"), []).append(o)
    return ativos


def _destino_xy(pedido):
    return _xy((pedido.get("payload") or {}).get("dropoff"))


def explicar_atraso(identificador, agora=None):
    """Explica a situação de atraso de um pedido com base nos DADOS (observações).
    O Fleetbase não registra o motivo real do atraso; por isso nada aqui afirma uma causa."""
    pedidos = _all_orders()
    o = _achar_pedido(pedidos, identificador)
    if not o:
        return {"erro": f"Pedido '{identificador}' não encontrado."}
    agora = agora or datetime.now(timezone.utc)
    sched = _parse_dt(o.get("scheduled_at"))
    status = (o.get("status") or "").lower()
    base = {"codigo": o.get("internal_id"), "status": o.get("status"), "agendado_para": _local(sched)}
    if not _is_active(o):
        return {**base, "atrasado": False,
                "explicacao": "O pedido já está encerrado (concluído ou cancelado): não há atraso a explicar."}
    if not sched:
        return {**base, "atrasado": False, "explicacao": "O pedido não tem horário agendado."}
    segundos = (agora - sched).total_seconds()
    if segundos <= 0:
        return {**base, "atrasado": False, "faltam_minutos": int(-segundos // 60),
                "explicacao": "O pedido ainda não está atrasado."}

    minutos = int(segundos // 60)
    motorista = o.get("driver_assigned") if isinstance(o.get("driver_assigned"), dict) else None
    obs = []
    if not motorista:
        obs.append("O pedido ainda não tem motorista designado.")
        acao = ("Atribuir um motorista: use sugerir_motorista para escolher e propor_atribuicao "
                "para propor (o usuário confirma).")
        nome = None
    else:
        nome = motorista.get("name")
        outros = [p for p in (_ativos_por_motorista(pedidos).get(motorista.get("id") or nome) or [])
                  if p.get("id") != o.get("id")]
        outros_atrasados = [p for p in outros if _is_late(p, agora)]
        if status == "dispatched":
            obs.append(f"O pedido está despachado para {nome}, mas ainda não foi iniciado (não saiu para entrega).")
        elif status == "started":
            obs.append(f"O pedido está em rota com {nome} e já passou do horário agendado.")
        else:
            obs.append(f"O pedido está com a situação '{o.get('status')}' e já passou do horário agendado.")
        if outros:
            obs.append(f"{nome} tem outros {len(outros)} pedido(s) ativo(s), dos quais "
                       f"{len(outros_atrasados)} também atrasado(s).")
        else:
            obs.append(f"{nome} não tem outros pedidos ativos.")
        acao = f"Entrar em contato com {nome} para saber a situação do pedido."
    return {**base, "atrasado": True, "minutos_de_atraso": minutos, "atraso": _duracao(minutos),
            "motorista": nome, "observacoes": obs, "faixa_da_politica": faixa_de_atraso(minutos),
            "acao_sugerida": acao,
            "aviso": "O sistema não registra o motivo real do atraso (trânsito, problema com o motorista etc.): "
                     "as observações vêm apenas dos dados."}


def sugerir_motorista(identificador, max_sugestoes=3, agora=None):
    """Ordena motoristas candidatos para um pedido sem motorista, com o motivo. NÃO atribui nada.

    Critério: livres primeiro; entre ocupados, menos pedidos atrasados, depois menos pedidos
    ativos e, no empate, a entrega mais próxima do destino deste pedido. Não há GPS dos motoristas."""
    pedidos = _all_orders()
    o = _achar_pedido(pedidos, identificador)
    if not o:
        return {"erro": f"Pedido '{identificador}' não encontrado."}
    codigo = o.get("internal_id")
    if not _is_active(o):
        return {"erro": f"O pedido {codigo} já está {o.get('status')} e não precisa de motorista."}
    atual = o.get("driver_assigned")
    if isinstance(atual, dict):
        return {"erro": f"O pedido {codigo} já tem o motorista {atual.get('name')}. "
                        "Trocar motorista não é suportado."}
    drivers = _items(_get("/drivers", {"limit": 100}))
    if not drivers:
        return {"erro": "Não há motoristas cadastrados."}
    agora = agora or datetime.now(timezone.utc)
    ativos_map = _ativos_por_motorista(pedidos)
    alvo = _destino_xy(o)
    candidatos = []
    for d in drivers:
        ativos = ativos_map.get(d.get("id")) or ativos_map.get(d.get("name")) or []
        proximidade = None
        if alvo and ativos:
            distancias = [_haversine_km(alvo, xy) for xy in (_destino_xy(p) for p in ativos) if xy]
            proximidade = round(min(distancias), 1) if distancias else None
        candidatos.append({"id": d.get("id"), "nome": d.get("name"), "ativos": len(ativos),
                           "atrasados": sum(1 for p in ativos if _is_late(p, agora)), "prox": proximidade})
    candidatos.sort(key=lambda c: (c["ativos"] > 0, c["atrasados"], c["ativos"],
                                   c["prox"] if c["prox"] is not None else float("inf"), c["nome"] or ""))
    try:
        limite = max(1, min(int(max_sugestoes), 5))
    except (TypeError, ValueError):
        limite = 3
    sugestoes = []
    for i, c in enumerate(candidatos[:limite], 1):
        if c["ativos"] == 0:
            situacao, motivo = "livre", "livre: nenhum pedido ativo"
        else:
            situacao = "ocupado"
            motivo = f"ocupado: {c['ativos']} pedido(s) ativo(s)"
            if c["atrasados"]:
                motivo += f", {c['atrasados']} atrasado(s)"
            if c["prox"] is not None:
                motivo += f"; já tem entrega a {c['prox']} km do destino deste pedido"
        sugestoes.append({"posicao": i, "id": c["id"], "nome": c["nome"], "situacao": situacao,
                          "pedidos_ativos": c["ativos"], "pedidos_atrasados": c["atrasados"],
                          "entrega_mais_proxima_km": c["prox"], "motivo": motivo})
    resposta = {"pedido": codigo, "recomendado": sugestoes[0]["nome"], "sugestoes": sugestoes,
                "criterio": "Livres primeiro; entre os ocupados, menos pedidos atrasados, depois menos pedidos "
                            "ativos e, no empate, a entrega mais próxima deste destino. Não há GPS dos motoristas."}
    if candidatos[0]["ativos"] > 0:
        resposta["observacao"] = ("Nenhum motorista está livre: todos têm pedidos ativos. "
                                  "O recomendado é o menos carregado.")
    return resposta


# ---------- atribuição de motorista (escrita) ----------

def _achar_motorista(motoristas, texto):
    """Acha o motorista por id ou nome (sem acento/maiúscula; aceita só parte do nome).
    Devolve (motorista, erro)."""
    q = _norm(texto)
    if not q:
        return None, "Informe o motorista."
    for d in motoristas:
        if q == _norm(d.get("id")) or q == _norm(d.get("name")):
            return d, None
    partes = q.split()
    achados = [d for d in motoristas if all(p in _norm(d.get("name")) for p in partes)]
    if len(achados) == 1:
        return achados[0], None
    if not achados:
        return None, f"Motorista '{texto}' não encontrado."
    nomes = ", ".join(d.get("name", "?") for d in achados)
    return None, f"Mais de um motorista corresponde a '{texto}': {nomes}. Especifique melhor."


def preparar_atribuicao(pedido, motorista):
    """Valida a atribuição SEM gravar nada. Devolve os dados do rascunho ou {'erro': ...}."""
    pedidos = _all_orders()
    o = _achar_pedido(pedidos, pedido)
    if not o:
        return {"erro": f"Pedido '{pedido}' não encontrado."}
    if not _is_active(o):
        return {"erro": f"O pedido {o.get('internal_id')} já está {o.get('status')} e não pode receber motorista."}
    atual = o.get("driver_assigned")
    if isinstance(atual, dict):
        return {"erro": f"O pedido {o.get('internal_id')} já tem o motorista {atual.get('name')}. "
                        "Trocar motorista não é suportado nesta versão."}
    d, erro = _achar_motorista(_items(_get("/drivers", {"limit": 100})), motorista)
    if erro:
        return {"erro": erro}
    ocupado_com = []
    for p in pedidos:
        dp = p.get("driver_assigned")
        if isinstance(dp, dict) and _is_active(p) and (dp.get("id") == d.get("id")
                                                       or dp.get("name") == d.get("name")):
            ocupado_com.append(p.get("internal_id"))
    avisos = []
    if ocupado_com:
        avisos.append(f"{d.get('name')} já tem {len(ocupado_com)} pedido(s) ativo(s): "
                      f"{', '.join(ocupado_com)}.")
    return {"pedido_id": o["id"], "pedido_codigo": o.get("internal_id"),
            "motorista_id": d["id"], "motorista_nome": d.get("name"),
            "despachar": (o.get("status") or "").lower() == "created",
            "avisos": avisos, "pedido": _summarize_order(o)}


def _pedido_por_id(pedido_id):
    for o in _all_orders():
        if o.get("id") == pedido_id:
            return o
    return None


def atribuir_motorista(pedido_id, motorista_id, motorista_nome=None, despachar=True):
    """Grava a atribuição e RELÊ o pedido para confirmar que realmente ficou atribuída.
    Só deve ser chamada depois da confirmação do usuário."""
    _send("PUT", f"/orders/{pedido_id}", {"driver": motorista_id})
    o = _pedido_por_id(pedido_id)
    drv = o.get("driver_assigned") if o else None
    if not (isinstance(drv, dict) and (drv.get("id") == motorista_id
                                       or (motorista_nome and drv.get("name") == motorista_nome))):
        raise FleetbaseError("O Fleetbase respondeu, mas o motorista não ficou atribuído ao pedido. "
                             "Confira no console.")
    avisos = []
    if despachar and (o.get("status") or "").lower() == "created":
        try:
            _send("PATCH", f"/orders/{pedido_id}/dispatch")
            o = _pedido_por_id(pedido_id) or o
        except FleetbaseError as e:
            avisos.append(f"Motorista atribuído, mas o despacho falhou: {e}")
    return {"pedido": _summarize_order(o), "avisos": avisos}


if __name__ == "__main__":
    import json
    for nome, fn in [("resumo", resumo_do_dia), ("atrasados", listar_atrasados),
                     ("motoristas livres", lambda: listar_motoristas(apenas_livres=True))]:
        print(f"\n=== {nome} ===")
        print(json.dumps(fn(), ensure_ascii=False, indent=2))
