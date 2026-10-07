"""Teste de mutação da melhoria 7.

Planta defeitos, um por vez, em fleetbase_client.py e confere se test_melhoria7.py falha.
O arquivo original é sempre restaurado no final (inclusive com Ctrl+C).

    python mutacao_melhoria7.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

ALVO = Path("fleetbase_client.py")
COPIA = Path(".fleetbase_client.original.bak")

MUTACOES = [
    ("ordem trocada: ativos antes de atrasados",
     'c["ativos"] > 0, c["atrasados"], c["ativos"],', 'c["ativos"] > 0, c["ativos"], c["atrasados"],'),
    ("livre/ocupado invertido",
     'c["ativos"] > 0,', 'c["ativos"] == 0,'),
    ("limite da faixa exclusivo (<)",
     "minutos <= limite", "minutos < limite"),
    ("faixa 30min-2h encurtada para 1h",
     '(120, "de 30 minutos', '(60, "de 30 minutos'),
    ("faixa 2-24h encurtada",
     '(1440, "de 2 a 24', '(1400, "de 2 a 24'),
    ("horário exato passa a contar como atraso",
     "if segundos <= 0:", "if segundos < 0:"),
    ("proximidade usa a mais distante",
     "min(distancias)", "max(distancias)"),
    ("teto de sugestões passa de 5 para 9",
     "min(int(max_sugestoes), 5)", "min(int(max_sugestoes), 9)"),
    ("aceita pedido que já tem motorista",
     "if isinstance(atual, dict):\n        return {\"erro\": f\"O pedido {codigo} já tem",
     "if False:\n        return {\"erro\": f\"O pedido {codigo} já tem"),
    ("pedido encerrado recebe sugestão",
     "if not _is_active(o):\n        return {\"erro\": f\"O pedido {codigo} já está",
     "if False:\n        return {\"erro\": f\"O pedido {codigo} já está"),
    ("contagem de atrasados do motorista zerada",
     "sum(1 for p in ativos if _is_late(p, agora))", "sum(0 for p in ativos if _is_late(p, agora))"),
    ("conta o próprio pedido entre os 'outros'",
     'if p.get("id") != o.get("id")]', "if True]"),
    ("aviso de causa desconhecida removido",
     '"aviso": "O sistema não registra', '"aviso_x": "O sistema não registra'),
    ("busca ignora código inexistente (devolve o primeiro pedido)",
     'o = _achar_pedido(pedidos, identificador)\n    if not o:\n        return {"erro": f"Pedido \'{identificador}\' não encontrado."}\n    codigo',
     'o = _achar_pedido(pedidos, identificador) or pedidos[0]\n    if not o:\n        return {"erro": f"Pedido \'{identificador}\' não encontrado."}\n    codigo'),
]


def rodar_testes():
    # -B: não grava/atualiza .pyc. Sem isso, duas mutações de mesmo tamanho gravadas no mesmo
    # segundo reaproveitam o .pyc da anterior e o defeito novo nunca chega a ser testado.
    r = subprocess.run([sys.executable, "-B", "test_melhoria7.py"], capture_output=True, text=True)
    return r.returncode == 0, r.stderr


def main():
    if not ALVO.exists():
        sys.exit("Rode dentro de ~/projeto-logistica (fleetbase_client.py não encontrado).")
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
        print("fleetbase_client.py restaurado.")


if __name__ == "__main__":
    main()
