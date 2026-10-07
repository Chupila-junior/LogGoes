"""Página de DESPACHO: mapa, pedidos, motoristas e assistente de IA do despachante.

É executada pelo roteador (app.py): streamlit run app.py
"""
import pydeck as pdk
import streamlit as st

import fleetbase_client as fb
import tools
from agent import Despachante, propostas_criadas

# Alturas (px) pensadas para caber sem rolagem num navegador de ~900 px de altura útil.
# Ajuste aqui se a sua tela for diferente (ou use o botão "Compacto" / Ctrl e menos no navegador).
ALTURA_NORMAL = {"mapa": 480, "chat": 460}
ALTURA_COMPACTA = {"mapa": 270, "chat": 230}
DESCONTO_PROPOSTA = 80   # a faixa de confirmação ocupa espaço: mapa e chat encolhem para não rolar
ALTURA_MINIMA = 180

STATUS_UM = {"created": "criado", "dispatched": "despachado", "started": "iniciado",
             "completed": "concluído", "canceled": "cancelado"}
BADGE = {"created": "🔵 Criado", "dispatched": "🟣 Despachado", "started": "🟠 Iniciado",
         "completed": "🟢 Concluído", "canceled": "⚪ Cancelado"}
COR = {"atrasado": [239, 68, 68, 230], "created": [59, 130, 246, 230],
       "dispatched": [139, 92, 246, 230], "started": [245, 158, 11, 230],
       "completed": [34, 197, 94, 200], "canceled": [148, 163, 184, 170]}
COR_COLETA = [250, 204, 21, 255]
FILTROS = ["Todos", "Atrasados", "Sem motorista", "Despachados", "Concluídos"]
EXEMPLOS = [
    "Quais pedidos estão atrasados?",
    "Qual motorista está livre?",
    "Quais pedidos atrasados estão sem motorista?",
    "Explique o atraso do pedido atrasado sem motorista e sugira um motorista",
    "Atribua um motorista livre ao pedido atrasado sem motorista",
    "Me dê um resumo do dia",
]


# ---------- recursos em cache ----------
@st.cache_resource(show_spinner="Conectando ao agente no Foundry...")
def get_bot():
    # Criado uma vez só: o Streamlit reexecuta o script a cada interação
    return Despachante()


@st.cache_data(ttl=15, show_spinner=False)
def get_painel():
    return fb.painel()


# ---------- funções auxiliares ----------
def nova_conversa(bot):
    pid = st.session_state.pop("proposta_pendente", None)
    if pid:
        tools.cancelar_proposta(pid)
    st.session_state.conversation_id = bot.nova_conversa()
    st.session_state.messages = []


def registrar_proposta(pid):
    """Só a última proposta vale: a anterior, se houver, é descartada."""
    antiga = st.session_state.get("proposta_pendente")
    if antiga and antiga != pid:
        tools.cancelar_proposta(antiga)
    st.session_state.proposta_pendente = pid


def status_de(p):
    return (p.get("status") or "").lower()


def situacao(p):
    return "🔴 Atrasado" if p["atrasado"] else BADGE.get(status_de(p), p.get("status") or "—")


def cor(p):
    return COR["atrasado"] if p["atrasado"] else COR.get(status_de(p), COR["created"])


def nome_de(lugar):
    return (lugar or {}).get("nome") or "—"


def passa_filtro(p, filtro):
    if filtro == "Atrasados":
        return p["atrasado"]
    if filtro == "Sem motorista":
        return p["ativo"] and not p["motorista"]
    if filtro == "Despachados":
        return status_de(p) == "dispatched"
    if filtro == "Concluídos":
        return status_de(p) == "completed"
    return True


ORDEM_URGENCIA = ["atrasado", "started", "dispatched", "created", "completed", "canceled"]


def chave_situacao(p):
    return "atrasado" if p["atrasado"] else (status_de(p) or "created")


def urgencia(p):
    k = chave_situacao(p)
    return ORDEM_URGENCIA.index(k) if k in ORDEM_URGENCIA else len(ORDEM_URGENCIA)


def situacao_texto(p):
    return "atrasado" if p["atrasado"] else STATUS_UM.get(status_de(p), p.get("status") or "?")


def construir_mapa(pedidos, so_ativos=True):
    """Mapa com um ponto por destino (cor = pedido mais urgente ali), coletas e rotas.

    Vários pedidos podem ter o mesmo destino; desenhá-los um a um faz um esconder o outro
    (um atrasado ficaria coberto por um pedido em dia), por isso eles são agrupados.
    Devolve (mapa, resumo) ou None se não houver pedido com coordenadas.
    """
    sel = [p for p in pedidos if p["destino_xy"] and (p["ativo"] or not so_ativos)]
    if not sel:
        return None
    por_destino = {}
    for p in sel:
        por_destino.setdefault(tuple(p["destino_xy"]), []).append(p)

    destinos, linhas, coletas = [], [], {}
    for xy, ps in por_destino.items():
        ps = sorted(ps, key=urgencia)                      # o mais urgente primeiro
        topo = ps[0]
        atrasados = sum(1 for p in ps if p["atrasado"])
        resumo = f"{len(ps)} pedido(s)" + (f" · {atrasados} atrasado(s)" if atrasados else "")
        codigos = ", ".join(f"{p['codigo']} ({situacao_texto(p)})" for p in ps[:5])
        if len(ps) > 5:
            codigos += f" e mais {len(ps) - 5}"
        texto = {"titulo": nome_de(topo["destino"]), "resumo": resumo, "codigos": codigos}
        destinos.append({**texto, "xy": list(xy), "cor": cor(topo), "ordem": urgencia(topo)})
        for origem in dict.fromkeys(tuple(p["origem_xy"]) for p in ps if p["origem_xy"]):
            doo = next(p for p in ps if p["origem_xy"] and tuple(p["origem_xy"]) == origem)
            linhas.append({**texto, "origem": list(origem), "destino": list(xy),
                           "cor": cor(doo), "ordem": urgencia(doo)})
            coletas.setdefault(origem, {"titulo": nome_de(doo["origem"]), "resumo": "Ponto de coleta",
                                        "codigos": "", "xy": list(origem), "cor": COR_COLETA})
    # os mais urgentes são desenhados por último, para ficarem por cima
    destinos.sort(key=lambda d: -d["ordem"])
    linhas.sort(key=lambda d: -d["ordem"])

    pontos = [d["xy"] for d in destinos] + [c["xy"] for c in coletas.values()]
    lat = sum(x[1] for x in pontos) / len(pontos)
    lng = sum(x[0] for x in pontos) / len(pontos)
    # Raios em METROS, com limites em pixels: o ponto não vira um círculo gigante ao aproximar.
    camadas = [
        pdk.Layer("LineLayer", linhas, get_source_position="origem", get_target_position="destino",
                  get_color="cor", get_width=2, width_min_pixels=2, width_max_pixels=4, pickable=True),
        pdk.Layer("ScatterplotLayer", destinos, get_position="xy", get_fill_color="cor",
                  get_radius=8, radius_min_pixels=6, radius_max_pixels=12, stroked=True,
                  get_line_color=[255, 255, 255, 230], line_width_min_pixels=1.5, pickable=True),
        pdk.Layer("ScatterplotLayer", list(coletas.values()), get_position="xy",
                  get_fill_color="cor", get_radius=12, radius_min_pixels=9, radius_max_pixels=16,
                  stroked=True, get_line_color=[17, 24, 39, 255], line_width_min_pixels=1.5, pickable=True),
    ]
    deck = pdk.Deck(
        layers=camadas, map_style="dark",
        initial_view_state=pdk.ViewState(latitude=lat, longitude=lng, zoom=10, pitch=0,
                                         min_zoom=8, max_zoom=19),
        tooltip={"html": "<b>{titulo}</b><br/>{resumo}<br/>{codigos}",
                 "style": {"backgroundColor": "#111827", "color": "#e5e7eb"}})
    return deck, {"pedidos": len(sel), "destinos": len(por_destino),
                  "atrasados": sum(1 for p in sel if p["atrasado"])}


def mostrar(msg):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("chamadas"):
            with st.expander("🔧 Consultas feitas pelo agente"):
                for c in msg["chamadas"]:
                    st.code(f"{c['funcao']}({c['argumentos']})", language="python")


# ---------- inicialização ----------
try:
    bot = get_bot()
except Exception as e:
    st.error(f"Não consegui iniciar o agente: {type(e).__name__}: {e}")
    st.info("Confira o `az login`, as variáveis do `.env` e as permissões no Foundry.")
    st.stop()

if "conversation_id" not in st.session_state:
    nova_conversa(bot)

# ---------- cabeçalho ----------
c_titulo, c_atualizar, c_nova, c_compacto = st.columns([6, 1.3, 1.6, 1.2])
c_titulo.markdown(
    '<div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap">'
    '<span style="font-size:1.6rem;font-weight:700">🚚 LogGoes · Despachante IA</span>'
    '<span style="opacity:.6;font-size:.8rem">Atribuições só são executadas depois da sua confirmação.</span>'
    '</div>', unsafe_allow_html=True)
if c_atualizar.button("🔄 Atualizar", use_container_width=True):
    get_painel.clear()
    st.rerun()
if c_nova.button("🗑️ Nova conversa", use_container_width=True):
    nova_conversa(bot)
    st.rerun()
compacto = c_compacto.toggle("Compacto", key="compacto", help="Alturas menores, para telas pequenas.")
base = ALTURA_COMPACTA if compacto else ALTURA_NORMAL
desconto = DESCONTO_PROPOSTA if st.session_state.get("proposta_pendente") else 0
alturas = {k: max(ALTURA_MINIMA, v - desconto) for k, v in base.items()}

card_slot = st.container()   # o cartão de confirmação é desenhado aqui, no fim do script

try:
    dados = get_painel()
except fb.FleetbaseError as e:
    dados = None
    st.warning(f"Fleetbase indisponível: {e}")

# ---------- indicadores ----------
if dados:
    r = dados["resumo"]
    k = st.columns(5)
    k[0].metric("Pedidos", r["total_pedidos"])
    k[1].metric("Atrasados", r["atrasados"])
    k[2].metric("Sem motorista", r["sem_motorista"])
    k[3].metric("Motoristas livres", f"{r['motoristas_livres']} de {r['motoristas_total']}")
    k[4].metric("Despachados", r["por_status"].get("dispatched", 0))

col_painel, col_chat = st.columns([3, 2])

# ---------- painel: mapa, pedidos, motoristas ----------
with col_painel:
    if not dados:
        st.info("Sem dados do Fleetbase no momento. Confira se o Docker está de pé e clique em Atualizar.")
    else:
        pedidos, motoristas = dados["pedidos"], dados["motoristas"]
        aba_mapa, aba_pedidos, aba_motoristas = st.tabs(["🗺️ Mapa", "📦 Pedidos", "🚚 Motoristas"])

        with aba_mapa:
            c_chk, c_info = st.columns([1.3, 4])
            so_ativos = c_chk.checkbox("Só pedidos ativos", value=True, key="so_ativos")
            resultado = construir_mapa(pedidos, so_ativos)
            if resultado is None:
                st.info("Nenhum pedido com coordenadas para mostrar no mapa.")
            else:
                mapa, info = resultado
                c_info.caption(f"{info['pedidos']} pedidos em {info['destinos']} destinos · "
                               f"{info['atrasados']} atrasados · cada ponto é um destino e a cor "
                               "mostra o pedido mais urgente nele")
                st.pydeck_chart(mapa, height=alturas["mapa"])
                st.caption("🔴 Atrasado · 🔵 Criado · 🟣 Despachado · 🟡 Ponto de coleta. "
                           "Sem posição de GPS dos motoristas na simulação.")

        with aba_pedidos:
            filtro = st.radio("Filtro", FILTROS, horizontal=True, label_visibility="collapsed",
                              key="filtro_pedidos")
            busca = st.text_input("Buscar", placeholder="Código, destino, motorista ou rastreio",
                                  label_visibility="collapsed", key="busca_pedidos").strip().lower()

            def bate_busca(p):
                alvo = " ".join(str(x or "") for x in [p["codigo"], p["rastreio"], p["motorista"],
                                                       nome_de(p["destino"])]).lower()
                return busca in alvo

            visiveis = [p for p in pedidos if passa_filtro(p, filtro) and bate_busca(p)]
            st.dataframe([{"Código": p["codigo"], "Situação": situacao(p),
                           "Motorista": p["motorista"] or "—", "Destino": nome_de(p["destino"]),
                           "Agendado": p["agendado_para"], "Rastreio": p["rastreio"]}
                          for p in visiveis], hide_index=True)
            st.caption(f"{len(visiveis)} de {len(pedidos)} pedidos")

            with st.expander("➕ Atribuir motorista a um pedido"):
                candidatos = {p["codigo"]: p for p in pedidos if p["ativo"] and not p["motorista"]}
                por_id = {m["id"]: m for m in motoristas}
                if not candidatos:
                    st.info("Nenhum pedido ativo sem motorista.")
                elif not motoristas:
                    st.info("Nenhum motorista cadastrado.")
                else:
                    ordem = [m["id"] for m in sorted(motoristas, key=lambda m: m["ocupado"])]
                    c1, c2, c3 = st.columns([3, 3, 1.3])
                    cod = c1.selectbox(
                        "Pedido", list(candidatos), key="sel_pedido",
                        format_func=lambda c: f"{c} · {nome_de(candidatos[c]['destino'])}"
                                              + (" · ⏰" if candidatos[c]["atrasado"] else ""))
                    mid = c2.selectbox(
                        "Motorista", ordem, key="sel_motorista",
                        format_func=lambda i: f"{por_id[i]['nome']} · "
                                              + ("🟠 ocupado" if por_id[i]["ocupado"] else "🟢 livre"))
                    if c3.button("Propor", key="btn_propor", type="primary", use_container_width=True):
                        res = tools.executar_ferramenta("propor_atribuicao", {"pedido": cod, "motorista": mid})
                        if "erro" in res:
                            st.error(res["erro"])
                        else:
                            registrar_proposta(res["proposta_id"])
                            st.rerun()   # redesenha com o mapa/chat reduzidos e a faixa de confirmação

        with aba_motoristas:
            ordenados = sorted(motoristas, key=lambda m: m["ocupado"])   # livres primeiro
            st.dataframe([{"Motorista": m["nome"],
                           "Situação": "🟠 Ocupado" if m["ocupado"] else "🟢 Livre",
                           "Pedidos ativos": ", ".join(m["pedidos_ativos"]) or "—",
                           "Veículo": m["veiculo"] or "—", "Placa": m["placa"] or "—",
                           "Telefone": m["telefone"] or "—"} for m in ordenados], hide_index=True)

# ---------- assistente ----------
with col_chat:
    st.markdown("**💬 Assistente**")
    with st.expander("💡 Exemplos de perguntas"):
        for ex in EXEMPLOS:
            if st.button(ex, use_container_width=True, key=f"ex_{ex}"):
                st.session_state.pending = ex

    caixa = st.container(height=alturas["chat"], border=True)
    with caixa:
        if not st.session_state.messages:
            st.caption("Pergunte sobre pedidos e motoristas, ou peça para atribuir um motorista.")
        for m in st.session_state.messages:
            mostrar(m)

    prompt = st.chat_input("Pergunte sobre pedidos ou motoristas...") or st.session_state.pop("pending", None)
    if prompt:
        user_msg = {"role": "user", "content": prompt}
        st.session_state.messages.append(user_msg)
        with caixa:
            mostrar(user_msg)
            with st.chat_message("assistant"):
                try:
                    with st.spinner("Consultando..."):
                        resposta, chamadas = bot.perguntar(prompt, st.session_state.conversation_id)
                except Exception as e:
                    resposta, chamadas = f"⚠️ Erro ao falar com o agente: {type(e).__name__}: {e}", []
                st.markdown(resposta)
                if chamadas:
                    with st.expander("🔧 Consultas feitas pelo agente"):
                        for c in chamadas:
                            st.code(f"{c['funcao']}({c['argumentos']})", language="python")
        st.session_state.messages.append({"role": "assistant", "content": resposta, "chamadas": chamadas})

        ids = propostas_criadas(chamadas)
        if ids:
            for extra in ids[:-1]:
                tools.cancelar_proposta(extra)
            registrar_proposta(ids[-1])
            st.rerun()   # redesenha com o mapa/chat reduzidos e a faixa de confirmação

# ---------- cartão de confirmação (a escrita só acontece aqui, no clique) ----------
pid = st.session_state.get("proposta_pendente")
if pid:
    with card_slot:
        prop = tools.obter_proposta(pid)
        if not prop:
            st.session_state.pop("proposta_pendente", None)
            st.warning("A proposta expirou. Peça de novo.")
        else:
            ped = prop["pedido"]
            with st.container(border=True):
                c_txt, c_ok, c_no = st.columns([6, 1.4, 1.1])
                linha = (f"📝 Atribuir **{prop['motorista_nome']}** ao pedido **{prop['pedido_codigo']}** · "
                         f"{nome_de(ped['origem'])} → {nome_de(ped['destino'])} · "
                         f"agendado para {ped['agendado_para']}"
                         + (" · ⏰ atrasado" if ped["atrasado"] else "")
                         + (" · o pedido também será despachado" if prop["despachar"] else ""))
                c_txt.markdown(linha)
                for aviso in prop["avisos"]:
                    c_txt.caption(f"⚠️ {aviso}")
                if c_ok.button("✅ Confirmar", type="primary", key=f"ok_{pid}", use_container_width=True):
                    r = tools.confirmar_proposta(pid)
                    st.session_state.pop("proposta_pendente", None)
                    if r["ok"]:
                        novo = STATUS_UM.get(r["pedido"]["status"], r["pedido"]["status"])
                        texto = (f"✅ {r['motorista_nome']} foi atribuído ao pedido "
                                 f"{r['pedido_codigo']} (status: {novo}).")
                        texto += "".join(f"\n\n⚠️ {a}" for a in r["avisos"])
                    else:
                        texto = f"❌ Não foi possível atribuir: {r['erro']}"
                    st.session_state.messages.append({"role": "assistant", "content": texto, "chamadas": []})
                    get_painel.clear()
                    st.rerun()
                if c_no.button("Cancelar", key=f"no_{pid}", use_container_width=True):
                    tools.cancelar_proposta(pid)
                    st.session_state.pop("proposta_pendente", None)
                    st.session_state.messages.append({"role": "assistant",
                                                      "content": "Proposta cancelada. Nada foi alterado.",
                                                      "chamadas": []})
                    st.rerun()
