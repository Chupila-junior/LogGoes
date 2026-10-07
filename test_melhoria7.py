"""Testes da melhoria 7: explicar_atraso e sugerir_motorista.

Não usa Azure nem o Fleetbase: os dados são simulados (mock). Rodar com:
    python test_melhoria7.py
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import fleetbase_client as fb

AGORA = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc)
ALVO = [-46.65, -23.56]          # destino do pedido sem motorista
PERTO = [-46.66, -23.56]         # ~1 km do alvo
LONGE = [-46.30, -23.90]         # ~50 km do alvo


def pedido(codigo, minutos_atraso, status="created", motorista=None, dropoff=None):
    """minutos_atraso > 0: agendado no passado; < 0: agendado no futuro."""
    return {
        "id": "order_" + codigo.lower(),
        "internal_id": codigo,
        "status": status,
        "scheduled_at": (AGORA - timedelta(minutes=minutos_atraso)).isoformat(),
        "driver_assigned": ({"id": "driver_" + motorista.lower(), "name": motorista}
                            if motorista else None),
        "payload": {"dropoff": {"xy": dropoff} if dropoff else None},
    }


def motoristas(*nomes):
    return [{"id": "driver_" + n.lower(), "name": n} for n in nomes]


class Base(unittest.TestCase):
    def dados(self, pedidos, drivers=None):
        """Troca as fontes de dados por dados simulados e proíbe qualquer escrita."""
        patches = [
            patch.object(fb, "_all_orders", return_value=pedidos),
            patch.object(fb, "_get", return_value=drivers if drivers is not None else []),
            patch.object(fb, "_items", side_effect=lambda r: r),
            patch.object(fb, "_xy", side_effect=lambda p: p["xy"] if p else None),
            patch.object(fb, "_send", side_effect=AssertionError("escrita proibida")),
            patch.object(fb, "atribuir_motorista", side_effect=AssertionError("escrita proibida")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class TestFaixas(unittest.TestCase):
    def texto(self, minutos):
        return fb.faixa_de_atraso(minutos)

    def test_limites(self):
        self.assertIn("até 30 minutos", self.texto(0))
        self.assertIn("até 30 minutos", self.texto(30))
        self.assertIn("de 30 minutos a 2 horas", self.texto(31))
        self.assertIn("de 30 minutos a 2 horas", self.texto(120))
        self.assertIn("de 2 a 24 horas", self.texto(121))
        self.assertIn("de 2 a 24 horas", self.texto(1440))
        self.assertIn("mais de 24 horas", self.texto(1441))


class TestExplicarAtraso(Base):
    def test_sem_motorista(self):
        self.dados([pedido("LO100001", 90)])
        r = fb.explicar_atraso("LO100001", agora=AGORA)
        self.assertTrue(r["atrasado"])
        self.assertEqual(r["minutos_de_atraso"], 90)
        self.assertEqual(r["atraso"], "1 h 30 min")
        self.assertIn("de 30 minutos a 2 horas", r["faixa_da_politica"])
        self.assertTrue(any("não tem motorista" in o for o in r["observacoes"]))
        self.assertIn("sugerir_motorista", r["acao_sugerida"])

    def test_faixa_mais_de_24h(self):
        self.dados([pedido("LO100001", 30 * 60)])
        r = fb.explicar_atraso("LO100001", agora=AGORA)
        self.assertIn("mais de 24 horas", r["faixa_da_politica"])

    def test_nao_afirma_causa(self):
        self.dados([pedido("LO100001", 90)])
        r = fb.explicar_atraso("LO100001", agora=AGORA)
        self.assertIn("aviso", r)
        self.assertIn("não registra o motivo real", r["aviso"])

    def test_exatamente_no_horario_nao_e_atraso(self):
        self.dados([pedido("LO100001", 0)])
        r = fb.explicar_atraso("LO100001", agora=AGORA)
        self.assertFalse(r["atrasado"])

    def test_ainda_nao_atrasado(self):
        self.dados([pedido("LO100001", -45)])
        r = fb.explicar_atraso("LO100001", agora=AGORA)
        self.assertFalse(r["atrasado"])
        self.assertEqual(r["faltam_minutos"], 45)

    def test_encerrado_nao_tem_atraso(self):
        self.dados([pedido("LO100001", 500, status="completed")])
        r = fb.explicar_atraso("LO100001", agora=AGORA)
        self.assertFalse(r["atrasado"])
        self.assertIn("encerrado", r["explicacao"])

    def test_com_motorista_conta_outros_atrasados(self):
        self.dados([
            pedido("LO100001", 90, status="dispatched", motorista="Ana"),
            pedido("LO100002", 60, status="dispatched", motorista="Ana"),   # atrasado
            pedido("LO100003", -60, status="dispatched", motorista="Ana"),  # no prazo
        ])
        r = fb.explicar_atraso("LO100001", agora=AGORA)
        self.assertEqual(r["motorista"], "Ana")
        texto = " ".join(r["observacoes"])
        self.assertIn("outros 2 pedido(s) ativo(s)", texto)
        self.assertIn("1 também atrasado", texto)
        self.assertIn("não foi iniciado", texto)

    def test_inexistente(self):
        self.dados([pedido("LO100001", 90)])
        self.assertIn("erro", fb.explicar_atraso("LO999999", agora=AGORA))

    def test_codigo_em_minusculas(self):
        self.dados([pedido("LO100001", 90)])
        r = fb.explicar_atraso("lo100001", agora=AGORA)
        self.assertEqual(r["codigo"], "LO100001")


class TestSugerirMotorista(Base):
    def test_livre_antes_de_ocupado(self):
        self.dados([pedido("LO100001", 90),
                    pedido("LO100002", -30, status="dispatched", motorista="Ana")],
                   motoristas("Ana", "Beatriz"))
        r = fb.sugerir_motorista("LO100001", agora=AGORA)
        self.assertEqual(r["recomendado"], "Beatriz")
        self.assertEqual(r["sugestoes"][0]["situacao"], "livre")
        self.assertEqual(r["sugestoes"][1]["situacao"], "ocupado")
        self.assertNotIn("observacao", r)

    def test_menos_atrasados_vence_menos_ativos(self):
        # Ana: 1 ativo, atrasado. Bia: 2 ativos, nenhum atrasado. Bia deve vencer.
        self.dados([
            pedido("LO100001", 90),
            pedido("LO100002", 60, status="dispatched", motorista="Ana"),
            pedido("LO100003", -60, status="dispatched", motorista="Bia"),
            pedido("LO100004", -90, status="dispatched", motorista="Bia"),
        ], motoristas("Ana", "Bia"))
        r = fb.sugerir_motorista("LO100001", agora=AGORA)
        self.assertEqual(r["recomendado"], "Bia")
        self.assertIn("observacao", r)   # ninguém livre

    def test_menos_ativos_vence_quando_atrasados_empatam(self):
        self.dados([
            pedido("LO100001", 90),
            pedido("LO100002", -60, status="dispatched", motorista="Ana"),
            pedido("LO100003", -90, status="dispatched", motorista="Ana"),
            pedido("LO100004", -60, status="dispatched", motorista="Bia"),
        ], motoristas("Ana", "Bia"))
        self.assertEqual(fb.sugerir_motorista("LO100001", agora=AGORA)["recomendado"], "Bia")

    def test_empate_decidido_pela_entrega_mais_proxima(self):
        # "Zé" vem por último em ordem alfabética, mas tem a entrega mais próxima.
        self.dados([
            pedido("LO100001", 90, dropoff=ALVO),
            pedido("LO100002", -60, status="dispatched", motorista="Ana", dropoff=LONGE),
            pedido("LO100003", -60, status="dispatched", motorista="Zé", dropoff=PERTO),
        ], motoristas("Ana", "Zé"))
        r = fb.sugerir_motorista("LO100001", agora=AGORA)
        self.assertEqual(r["recomendado"], "Zé")
        self.assertLess(r["sugestoes"][0]["entrega_mais_proxima_km"],
                        r["sugestoes"][1]["entrega_mais_proxima_km"])
        self.assertIn("km do destino", r["sugestoes"][0]["motivo"])

    def test_proximidade_usa_a_entrega_mais_proxima_de_cada_motorista(self):
        # Os dois têm 2 pedidos ativos e 0 atrasados. "Zé" tem uma entrega a ~1 km e outra a ~50 km;
        # "Ana" tem duas a ~22 km. Vale a MAIS PRÓXIMA de cada um (Zé: 1 km vs Ana: 22 km).
        MEIO = [-46.50, -23.70]
        self.dados([
            pedido("LO100001", 90, dropoff=ALVO),
            pedido("LO100002", -60, status="dispatched", motorista="Ana", dropoff=MEIO),
            pedido("LO100003", -60, status="dispatched", motorista="Ana", dropoff=MEIO),
            pedido("LO100004", -60, status="dispatched", motorista="Zé", dropoff=PERTO),
            pedido("LO100005", -60, status="dispatched", motorista="Zé", dropoff=LONGE),
        ], motoristas("Ana", "Zé"))
        r = fb.sugerir_motorista("LO100001", agora=AGORA)
        self.assertEqual(r["recomendado"], "Zé")
        self.assertLess(r["sugestoes"][0]["entrega_mais_proxima_km"], 5)

    def test_motivo_cita_pedidos_atrasados_do_motorista(self):
        self.dados([
            pedido("LO100001", 90),
            pedido("LO100002", 60, status="dispatched", motorista="Ana"),
            pedido("LO100003", 70, status="dispatched", motorista="Ana"),
        ], motoristas("Ana"))
        s = fb.sugerir_motorista("LO100001", agora=AGORA)["sugestoes"][0]
        self.assertEqual(s["pedidos_atrasados"], 2)
        self.assertIn("2 atrasado(s)", s["motivo"])

    def test_limita_a_5_sugestoes(self):
        nomes = ["A1", "A2", "A3", "A4", "A5", "A6", "A7"]
        self.dados([pedido("LO100001", 90)], motoristas(*nomes))
        self.assertEqual(len(fb.sugerir_motorista("LO100001", max_sugestoes=99, agora=AGORA)["sugestoes"]), 5)
        self.assertEqual(len(fb.sugerir_motorista("LO100001", max_sugestoes=2, agora=AGORA)["sugestoes"]), 2)
        self.assertEqual(len(fb.sugerir_motorista("LO100001", max_sugestoes="x", agora=AGORA)["sugestoes"]), 3)

    def test_pedido_que_ja_tem_motorista(self):
        self.dados([pedido("LO100001", 90, status="dispatched", motorista="Ana")], motoristas("Ana"))
        r = fb.sugerir_motorista("LO100001", agora=AGORA)
        self.assertIn("erro", r)
        self.assertIn("Ana", r["erro"])

    def test_pedido_encerrado(self):
        self.dados([pedido("LO100001", 90, status="canceled")], motoristas("Ana"))
        self.assertIn("erro", fb.sugerir_motorista("LO100001", agora=AGORA))

    def test_pedido_inexistente(self):
        self.dados([pedido("LO100001", 90)], motoristas("Ana"))
        self.assertIn("erro", fb.sugerir_motorista("LO999999", agora=AGORA))

    def test_sem_motoristas(self):
        self.dados([pedido("LO100001", 90)], [])
        self.assertIn("erro", fb.sugerir_motorista("LO100001", agora=AGORA))

    def test_codigo_em_minusculas(self):
        self.dados([pedido("LO100001", 90)], motoristas("Ana"))
        self.assertEqual(fb.sugerir_motorista("lo100001", agora=AGORA)["pedido"], "LO100001")

    def test_nao_grava_nada(self):
        # self.dados() faz _send e atribuir_motorista levantarem AssertionError.
        self.dados([pedido("LO100001", 90)], motoristas("Ana", "Bia"))
        fb.sugerir_motorista("LO100001", agora=AGORA)
        fb.explicar_atraso("LO100001", agora=AGORA)


if __name__ == "__main__":
    unittest.main(verbosity=1)
