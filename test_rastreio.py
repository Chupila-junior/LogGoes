"""Testes de consultar_rastreio_publico (o que o CLIENTE FINAL enxerga) e normalizar_codigo.

Não usa o Fleetbase: pedidos e resumo são simulados.
    python test_rastreio.py
"""
import json
import unittest
from unittest.mock import patch

import fleetbase_client as fb

ARMAZENADO = "Gó8881740690BR"
CHAVES_PUBLICAS = {"rastreio", "situacao", "agendado_para", "atrasado", "motorista_designado", "bairro_destino"}


def pedido(rastreio=ARMAZENADO, status="dispatched", codigo="LO312007", id_="order_abc123xyz"):
    return {"id": id_, "internal_id": codigo, "status": status, "tracking": rastreio}


def resumo_base():
    return {"codigo": "LO312007", "id": "order_abc123xyz", "agendado_para": "06/10 19:40", "atrasado": True,
            "motorista": "Ana Costa", "telefone": "+55 11 99999-0000",
            "origem": {"nome": "CENTRO DE DISTRIBUIÇÃO"},
            "destino": {"bairro": "Moema", "nome": "CLIENTE MOEMA"}}


class Base(unittest.TestCase):
    def setUp(self):
        self.resumo = resumo_base()
        self.pedidos = [pedido()]
        self.leitura = patch.object(fb, "_all_orders", side_effect=lambda: self.pedidos).start()
        patch.object(fb, "_tracking_code", side_effect=lambda o: o.get("tracking")).start()
        patch.object(fb, "_summarize_order", side_effect=lambda o, now=None: dict(self.resumo)).start()
        self.addCleanup(patch.stopall)


class TestNormalizar(unittest.TestCase):
    def test_ignora_caixa_acento_espacos_e_separadores(self):
        esperado = fb.normalizar_codigo(ARMAZENADO)
        for variante in ["go8881740690br", "GÓ8881740690BR", "Gó-8881 740690 BR", " g.ó_88817-40690br "]:
            with self.subTest(variante=variante):
                self.assertEqual(fb.normalizar_codigo(variante), esperado)

    def test_codigos_diferentes_continuam_diferentes(self):
        self.assertNotEqual(fb.normalizar_codigo("LOG1456833839BR"), fb.normalizar_codigo("LOG1456833838BR"))


class TestPrivacidade(Base):
    def test_devolve_so_o_que_o_cliente_precisa(self):
        r = fb.consultar_rastreio_publico("Gó8881740690BR")
        self.assertEqual(set(r), CHAVES_PUBLICAS)
        self.assertEqual(r["rastreio"], ARMAZENADO)
        self.assertTrue(r["atrasado"])
        self.assertEqual(r["agendado_para"], "06/10 19:40")
        self.assertEqual(r["bairro_destino"], "Moema")
        self.assertIs(r["motorista_designado"], True)

    def test_nada_sensivel_vaza(self):
        texto = json.dumps(fb.consultar_rastreio_publico(ARMAZENADO), ensure_ascii=False)
        for segredo in ["Ana Costa", "99999", "CENTRO", "CLIENTE MOEMA", "order_abc", "LO312007"]:
            with self.subTest(segredo=segredo):
                self.assertNotIn(segredo, texto)

    def test_motorista_so_aparece_como_sim_ou_nao(self):
        for motorista in [None, ""]:
            with self.subTest(motorista=motorista):
                self.resumo["motorista"] = motorista
                self.assertIs(fb.consultar_rastreio_publico(ARMAZENADO)["motorista_designado"], False)

    def test_bairro_ausente(self):
        self.resumo["destino"] = None
        self.assertIsNone(fb.consultar_rastreio_publico(ARMAZENADO)["bairro_destino"])

    def test_atraso_e_horario_vem_do_resumo(self):
        self.resumo["atrasado"], self.resumo["agendado_para"] = False, "08/10 10:00"
        r = fb.consultar_rastreio_publico(ARMAZENADO)
        self.assertIs(r["atrasado"], False)
        self.assertEqual(r["agendado_para"], "08/10 10:00")


class TestBusca(Base):
    def test_ignora_caixa_acento_espacos_e_hifens(self):
        for variante in ["go8881740690br", "GÓ8881740690BR", "Gó-8881 740690 BR"]:
            with self.subTest(variante=variante):
                self.assertEqual(fb.consultar_rastreio_publico(variante)["rastreio"], ARMAZENADO)

    def test_so_aceita_o_codigo_de_rastreio_nao_o_interno_nem_o_id(self):
        for outro in ["LO312007", "order_abc123xyz", "ORDER ABC123XYZ"]:
            with self.subTest(outro=outro):
                r = fb.consultar_rastreio_publico(outro)
                self.assertIn("Não encontrei", r["erro"])
                self.assertNotIn("rastreio", r)

    def test_codigo_parcial_nao_acha_nada(self):
        # o código tem de ser o completo e exato (isso é o que torna inofensivo um "pedaço" que passe pela trava)
        for parcial in ["Gó888174069", "8881740690BR", "Gó8881740690BRX", "XGó8881740690BR"]:
            with self.subTest(parcial=parcial):
                self.assertIn("Não encontrei", fb.consultar_rastreio_publico(parcial)["erro"])

    def test_acha_o_pedido_certo_entre_varios_e_ignora_os_sem_rastreio(self):
        self.pedidos = [pedido(rastreio=None, codigo="LO000001"), pedido(rastreio="", codigo="LO000002"),
                        pedido(rastreio="LOG1111111111BR", codigo="LO000003"), pedido()]
        self.assertEqual(fb.consultar_rastreio_publico(ARMAZENADO)["rastreio"], ARMAZENADO)
        self.assertEqual(fb.consultar_rastreio_publico("log1111111111br")["rastreio"], "LOG1111111111BR")

    def test_situacao_traduzida_para_o_cliente(self):
        for status in ["created", "dispatched", "started", "completed", "canceled"]:
            with self.subTest(status=status):
                self.pedidos = [pedido(status=status)]
                self.assertEqual(fb.consultar_rastreio_publico(ARMAZENADO)["situacao"], fb.STATUS_CLIENTE[status])

    def test_status_em_maiusculas_e_desconhecido(self):
        self.pedidos = [pedido(status="Dispatched")]
        self.assertEqual(fb.consultar_rastreio_publico(ARMAZENADO)["situacao"], fb.STATUS_CLIENTE["dispatched"])
        self.pedidos = [pedido(status="xyz")]
        self.assertEqual(fb.consultar_rastreio_publico(ARMAZENADO)["situacao"], "xyz")
        self.pedidos = [pedido(status=None)]
        self.assertEqual(fb.consultar_rastreio_publico(ARMAZENADO)["situacao"], "indisponível")


class TestEntradaInvalida(Base):
    def test_formato_invalido_nem_consulta_os_pedidos(self):
        for codigo in ["", "   ", "ABC", "A" * 7, "A" * 25, "A" * 40, "Gó888174!!!!0690BR", "'; DROP TABLE x;--"]:
            with self.subTest(codigo=codigo):
                r = fb.consultar_rastreio_publico(codigo)
                self.assertIn("inválido", r["erro"])
        self.leitura.assert_not_called()

    def test_limites_de_tamanho_8_e_24(self):
        for tamanho in (8, 24):
            with self.subTest(tamanho=tamanho):
                r = fb.consultar_rastreio_publico("A" * tamanho)
                self.assertIn("Não encontrei", r["erro"])     # formato aceito, mas não existe


class TestNaoEncontrado(Base):
    def test_mensagem_generica_e_mostra_o_codigo_consultado(self):
        r = fb.consultar_rastreio_publico("Gó8881740691BR")      # um dígito trocado
        self.assertEqual(set(r), {"erro", "codigo_consultado"})
        self.assertEqual(r["erro"], "Não encontrei um pedido com esse código de rastreio.")
        self.assertEqual(r["codigo_consultado"], "GO8881740691BR")

    def test_resposta_igual_para_codigo_inexistente_e_para_codigo_interno(self):
        # não confirma se o código existe em outro formato
        a = fb.consultar_rastreio_publico("ZZZZZZZZ1111")
        b = fb.consultar_rastreio_publico("LO312007")
        self.assertEqual(a["erro"], b["erro"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
