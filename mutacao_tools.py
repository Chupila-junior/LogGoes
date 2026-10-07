"""Teste de mutação de tools.py: planta defeitos, um por vez, e confere se test_tools.py falha.
O arquivo original é sempre restaurado no final (inclusive com Ctrl+C).

    python mutacao_tools.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

ALVO = Path("tools.py")
COPIA = Path(".tools.original.bak")
TESTE = "test_tools.py"

MUTACOES = [
    ("proposta vale 100 minutos", "VALIDADE_S = 600", "VALIDADE_S = 6000"),
    ("expira só depois do limite (>=)", "> VALIDADE_S", ">= VALIDADE_S"),
    ("proposta não é de uso único", "prop = PROPOSTAS.pop(proposta_id, None)", "prop = PROPOSTAS.get(proposta_id)"),
    ("grava com o id errado do motorista",
     'prop["pedido_id"], prop["motorista_id"],', 'prop["pedido_id"], prop["motorista_nome"],'),
    ("confirmar não limpa expiradas",
     "    _limpar_expiradas()\n    prop = PROPOSTAS.pop(proposta_id, None)", "    prop = PROPOSTAS.pop(proposta_id, None)"),
    ("proposta nasce já expirada", '"criada_em": time.time()}', '"criada_em": 0}'),
    ("entradas do modelo não são truncadas", "fb.preparar_atribuicao(pedido[:60], motorista[:80])",
     "fb.preparar_atribuicao(pedido, motorista)"),
    ("erro do Fleetbase vira proposta", '    if "erro" in r:\n        return r', '    if False:\n        return r'),
    ("status inválido passa", "if status not in STATUS_VALIDOS:", "if False:"),
    ("'todos' deixa de remover o filtro", 'None if status == "todos" else status', "status"),
    ("status padrão deixa de ser 'todos'", 'args.get("status", "todos")', 'args.get("status", "created")'),
    ("consultar_pedido sem truncar", "fb.consultar_pedido(ident[:60])", "fb.consultar_pedido(ident)"),
    ("limite de itens exclusivo (>=)", "len(dados) > MAX_ITENS", "len(dados) >= MAX_ITENS"),
    ("exceção inesperada vaza a mensagem (executar)",
     'f"Falha inesperada em {nome}: {type(e).__name__}"', 'f"Falha inesperada em {nome}: {type(e).__name__}: {e}"'),
    ("exceção inesperada vaza a mensagem (confirmar)",
     'f"Falha inesperada: {type(e).__name__}"', 'f"Falha inesperada: {type(e).__name__}: {e}"'),
    ("instrução diz que foi executado", '"instrucao": "NADA foi executado.', '"instrucao": "Foi executado.'),
    ("cancelar não cancela", "def cancelar_proposta(proposta_id):\n    PROPOSTAS.pop(proposta_id, None)",
     "def cancelar_proposta(proposta_id):\n    pass"),
    ("obter_proposta ignora expiração",
     "    _limpar_expiradas()\n    return PROPOSTAS.get(proposta_id)", "    return PROPOSTAS.get(proposta_id)"),
    ("explicar e sugerir trocados",
     'fb.explicar_atraso if nome == "explicar_atraso" else', 'fb.explicar_atraso if nome != "explicar_atraso" else'),
    ("apenas_livres padrão vira True",
     'bool(args.get("apenas_livres", False))', 'bool(args.get("apenas_livres", True))'),
    ("modelo grava direto (propor já confirma)",
     "            return _propor(args)",
     '            p = _propor(args)\n            return confirmar_proposta(p["proposta_id"]) if "proposta_id" in p else p'),
]


def rodar_testes():
    # -B: não grava .pyc (evita reaproveitar o .pyc de uma mutação anterior de mesmo tamanho).
    r = subprocess.run([sys.executable, "-B", TESTE], capture_output=True, text=True)
    return r.returncode == 0, r.stderr


def main():
    if not ALVO.exists():
        sys.exit("Rode dentro de ~/projeto-logistica (tools.py não encontrado).")
    original = ALVO.read_text(encoding="utf-8")
    shutil.copy(ALVO, COPIA)
    try:
        ok, saida = rodar_testes()
        if not ok:
            print("A suíte já FALHA sem nenhum defeito plantado. Corrija isso antes:\n")
            print(saida[-1500:])
            return
        print("Suíte passa sem defeitos. Plantando defeitos...\n")
        detectados, inaplicaveis = 0, []
        for nome, antigo, novo in MUTACOES:
            if original.count(antigo) != 1:
                inaplicaveis.append(nome)
                print(f"  ?  não aplicável (trecho não encontrado ou ambíguo): {nome}")
                continue
            ALVO.write_text(original.replace(antigo, novo), encoding="utf-8")
            ok, _ = rodar_testes()
            if ok:
                print(f"  ✖  NÃO DETECTADO: {nome}")
            else:
                detectados += 1
                print(f"  ✔  detectado: {nome}")
        aplicadas = len(MUTACOES) - len(inaplicaveis)
        print(f"\nResultado: {detectados}/{aplicadas} defeitos detectados"
              + (f" ({len(inaplicaveis)} não aplicáveis)" if inaplicaveis else ""))
    finally:
        ALVO.write_text(original, encoding="utf-8")
        COPIA.unlink(missing_ok=True)
        print("tools.py restaurado.")


if __name__ == "__main__":
    main()
