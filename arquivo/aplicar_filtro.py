"""Mensagem própria para quando o filtro de conteúdo do Azure bloqueia a pergunta (erro 400 content_filter).

Hoje o usuário vê "erro inesperado". Passa a ver: "Não posso processar essa mensagem...".
Altera só agent.py (precisa já ter a resiliência v2). Confere os trechos antes de gravar, guarda cópia em
~/antes_filtro/ (nunca sobrescreve cópia existente) e não faz nada se já foi aplicado.
    python aplicar_filtro.py
"""
import shutil
import sys
import time
from pathlib import Path

TROCAS = [
    ('    "servidor": "O serviço de IA está instável no momento. Tente de novo em instantes.",\n',
     '    "servidor": "O serviço de IA está instável no momento. Tente de novo em instantes.",\n'
     '    "filtro": "Não posso processar essa mensagem. Pergunte sobre as entregas da LogGoes.",\n'),
    ('    if status in (401, 403) or any(p in nome for p in ("authentication", "credential", "permission")):\n',
     '    if getattr(exc, "code", None) == "content_filter" or "content_filter" in str(exc):\n'
     '        return "filtro"   # o filtro de conteúdo do Azure bloqueou a mensagem (nenhuma ferramenta rodou)\n'
     '    if status in (401, 403) or any(p in nome for p in ("authentication", "credential", "permission")):\n'),
]

p = Path("agent.py")
if not p.exists():
    sys.exit("ABORTADO: agent.py não encontrado. Rode dentro de ~/projeto-logistica.")
t = p.read_text(encoding="utf-8")
if '"filtro"' in t:
    sys.exit("Já aplicado, nada a fazer.")
if "conversa_efetiva" not in t:
    sys.exit("ABORTADO (nada foi alterado): aplique antes a resiliência (python aplicar_resiliencia.py).")
for antigo, novo in TROCAS:
    if t.count(antigo) != 1:
        sys.exit(f"ABORTADO (nada foi alterado): trecho apareceu {t.count(antigo)} vez(es), esperado 1:\n{antigo}")
    t = t.replace(antigo, novo)
compile(t, "agent.py", "exec")
pasta = Path.home() / "antes_filtro"
pasta.mkdir(exist_ok=True)
copia = pasta / "agent.py"
if copia.exists():
    copia = pasta / f"agent.py.{time.strftime('%Y%m%d_%H%M%S')}"
shutil.copy(p, copia)
p.write_text(t, encoding="utf-8")
print(f"agent.py alterado (cópia do original em {copia}).")
print("Teste: python test_filtro.py")
