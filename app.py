"""Painel LogGoes: ponto de entrada com navegação entre as páginas.

Rodar:  streamlit run app.py
Requer: az login válido, Fleetbase rodando e o .env preenchido.

Páginas:
- pagina_despacho.py: painel do despachante (interno): mapa, pedidos, motoristas, atribuições.
- pagina_atendimento.py: atendimento ao cliente (externo): base de conhecimento e rastreio.
"""
import streamlit as st

st.set_page_config(page_title="LogGoes · Despacho e Atendimento", page_icon="🚚", layout="wide")
st.markdown("""
<style>
/* topo: espaço para a barra fixa do Streamlit (senão ela cobre os botões); fim: sem folga enorme */
.block-container, [data-testid="stMainBlockContainer"] {
    padding-top: 3.6rem !important; padding-bottom: 1rem !important;
    padding-left: 1.5rem !important; padding-right: 1.5rem !important;}
[data-testid="stAppDeployButton"], .stAppDeployButton {display: none;}
div[data-testid="stMetric"] {background: #171f2e; border: 1px solid #243049;
                             padding: 6px 12px; border-radius: 10px;}
div[data-testid="stMetricValue"] {font-size: 1.9rem;}
</style>
""", unsafe_allow_html=True)

paginas = [
    st.Page("pagina_despacho.py", title="Despacho", icon="🚚", default=True),
    st.Page("pagina_atendimento.py", title="Atendimento ao cliente", icon="💬"),
]
pg = st.navigation(paginas, position="top")   # o menu fica no cabeçalho: não ocupa altura da página
pg.run()
