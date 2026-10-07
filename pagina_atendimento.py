"""Página de ATENDIMENTO AO CLIENTE: base de conhecimento (busca em arquivos) + rastreio.

É executada pelo roteador (app.py): streamlit run app.py
Requer a base publicada: python atendimento.py --publicar-base
"""
import streamlit as st

import fleetbase_client as fb
from atendimento import PASTA_BASE, Atendente

# Altura (px) do chat, pensada para caber sem rolagem num navegador de ~900 px de altura útil.
ALTURA_NORMAL = {"chat": 480}
ALTURA_COMPACTA = {"chat": 260}

EXEMPLOS = [
    "O que acontece se meu pedido atrasar?",
    "Como posso cancelar um pedido?",
    "O que significa o status \"criado\"?",
    "Posso mudar o endereço de entrega?",
    "Quem é o motorista do meu pedido?",
]
SITUACAO_CURTA = {"created": "🔵 criado", "dispatched": "🟣 despachado", "started": "🟠 iniciado",
                  "completed": "🟢 concluído", "canceled": "⚪ cancelado"}


@st.cache_resource(show_spinner="Conectando ao agente de atendimento...")
def get_atendente():
    # Criado uma vez só: o Streamlit reexecuta o script a cada interação
    return Atendente()


@st.cache_data(ttl=15, show_spinner=False)
def get_codigos_teste():
    """Códigos de rastreio reais para a demonstração (atrasados primeiro). Vazio se o Fleetbase estiver fora."""
    try:
        pedidos = [p for p in fb.painel()["pedidos"] if p["rastreio"]]
    except fb.FleetbaseError:
        return []
    pedidos.sort(key=lambda p: (not p["atrasado"], not p["ativo"]))
    return [{"rastreio": p["rastreio"],
             "situacao": "🔴 atrasado" if p["atrasado"]
             else SITUACAO_CURTA.get((p["status"] or "").lower(), p["status"] or "?")}
            for p in pedidos[:5]]


def mostrar(msg):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("chamadas"):
            with st.expander("🔧 Consulta ao pedido"):
                for c in msg["chamadas"]:
                    st.code(f"{c['funcao']}({c['argumentos']})", language="python")
        if msg.get("fontes"):
            st.caption("📄 Fontes: " + ", ".join(msg["fontes"]))


# ---------- inicialização ----------
try:
    bot = get_atendente()
except RuntimeError as e:   # em geral: a base de conhecimento ainda não foi publicada
    st.error(str(e))
    st.stop()
except Exception as e:
    st.error(f"Não consegui iniciar o atendimento: {type(e).__name__}: {e}")
    st.info("Confira o `az login`, as variáveis do `.env` e se a base foi publicada "
            "(`python atendimento.py --publicar-base`).")
    st.stop()


def nova_conversa():
    st.session_state.conv_atend = bot.nova_conversa()
    st.session_state.msgs_atend = []


if "conv_atend" not in st.session_state:
    nova_conversa()

# ---------- cabeçalho ----------
c_titulo, c_nova, c_compacto = st.columns([7, 1.6, 1.2])
c_titulo.markdown(
    '<div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap">'
    '<span style="font-size:1.6rem;font-weight:700">💬 Atendimento ao cliente</span>'
    '<span style="opacity:.6;font-size:.8rem">Respostas baseadas nos documentos da LogGoes e na '
    'consulta de rastreio.</span></div>', unsafe_allow_html=True)
if c_nova.button("🗑️ Nova conversa", use_container_width=True, key="nova_atend"):
    nova_conversa()
    st.rerun()
compacto = c_compacto.toggle("Compacto", key="compacto", help="Altura menor, para telas pequenas.")
altura = (ALTURA_COMPACTA if compacto else ALTURA_NORMAL)["chat"]

col_chat, col_info = st.columns([3, 2])

# ---------- chat ----------
with col_chat:
    st.markdown("**💬 Conversa**")
    with st.expander("💡 Exemplos de perguntas"):
        for ex in EXEMPLOS:
            if st.button(ex, use_container_width=True, key=f"exa_{ex}"):
                st.session_state.pending_atend = ex

    caixa = st.container(height=altura, border=True)
    with caixa:
        if not st.session_state.msgs_atend:
            st.caption("Olá! Posso ajudar com prazos, atrasos, reagendamento, cancelamento e com o "
                       "acompanhamento do seu pedido. Para consultar um pedido, informe o código de rastreio.")
        for m in st.session_state.msgs_atend:
            mostrar(m)

    prompt = st.chat_input("Escreva sua dúvida ou o código de rastreio...",
                           key="chat_atend") or st.session_state.pop("pending_atend", None)
    if prompt:
        user_msg = {"role": "user", "content": prompt}
        st.session_state.msgs_atend.append(user_msg)
        with caixa:
            mostrar(user_msg)
            with st.chat_message("assistant"):
                try:
                    with st.spinner("Consultando..."):
                        resposta, chamadas, fontes = bot.perguntar(prompt, st.session_state.conv_atend)
                except Exception as e:   # ao cliente, só o essencial (sem detalhes técnicos)
                    resposta, chamadas, fontes = (f"Desculpe, não consegui responder agora "
                                                  f"({type(e).__name__}). Tente novamente em instantes."), [], []
                st.markdown(resposta)
                if chamadas:
                    with st.expander("🔧 Consulta ao pedido"):
                        for c in chamadas:
                            st.code(f"{c['funcao']}({c['argumentos']})", language="python")
                if fontes:
                    st.caption("📄 Fontes: " + ", ".join(fontes))
        st.session_state.msgs_atend.append({"role": "assistant", "content": resposta,
                                            "chamadas": chamadas, "fontes": fontes})

# ---------- informações ----------
with col_info:
    st.markdown("**ℹ️ Sobre este atendimento**")
    st.markdown("- 📚 Responde com base em **documentos** (prazos, atrasos, reagendamento, cancelamento, "
                "avarias).\n"
                "- 📦 **Consulta o pedido** pelo código de rastreio.\n"
                "- 🚫 **Não** altera pedidos, não concede reembolsos e não informa dados de motoristas.")

    ultima_resposta = next((m for m in reversed(st.session_state.msgs_atend) if m["role"] == "assistant"), None)
    ultimas = (ultima_resposta.get("fontes") or []) if ultima_resposta else []
    st.markdown("**📄 Fontes da última resposta**")
    if ultimas:
        for nome in ultimas:
            st.markdown(f"- `{nome}`")
    else:
        st.caption("Nenhuma ainda: as fontes aparecem quando a resposta usa os documentos.")

    docs = sorted(p.name for p in PASTA_BASE.glob("*.md"))
    with st.expander(f"📚 Documentos da base ({len(docs)})"):
        for nome in docs:
            st.markdown(f"- `{nome}`")

    with st.expander("🧪 Códigos de teste (apenas demonstração)"):
        codigos = get_codigos_teste()
        if not codigos:
            st.caption("Sem códigos: o Fleetbase não respondeu ou não há pedidos.")
        for item in codigos:
            if st.button(f"{item['rastreio']} · {item['situacao']}", use_container_width=True,
                         key=f"cod_{item['rastreio']}"):
                st.session_state.pending_atend = f"Onde está o meu pedido {item['rastreio']}?"
                st.rerun()   # esta coluna é desenhada depois do campo de digitar: reexecuta para enviar
