"""Testes do caminho de escrita em fleetbase_client.py:
_achar_motorista, preparar_atribuicao (não grava) e atribuir_motorista (grava e confere).

Não usa o Fleetbase: leituras e escritas da API são simuladas.
    python test_escrita.py
"""
import unittest
from unittest.mock import MagicMock, call, patch

import fleetbase_client as fb


def pedido(codigo, status="created", motorista=None):
    return {"id": "order_" + codigo.lower(), "internal_id": codigo, "status": status,
            "driver_assigned": motorista}


def drv(nome, id_=None):
    return {"id": id_ or "driver_" + nome.split()[0].lower(), "name": nome}


EQUIPE = [drv("Ana Costa"), drv("Beatriz Rocha"), drv("Pedro Lima"), drv("Pedro Alves")]


def resumo(o, now=None):
    return {"codigo": o.get("internal_id"), "status": o.get("status")}


class TestAcharMotorista(unittest.TestCase):
    def test_texto_vazio(self):
        for texto in ["", "   "]:
            self.assertEqual(fb._achar_motorista(EQUIPE, texto), (None, "Informe o motorista."))

    def test_por_id_exato(self):
        d, erro = fb._achar_motorista(EQUIPE, "driver_beatriz")
        self.assertEqual((d["name"], erro), ("Beatriz Rocha", None))

    def test_nome_sem_acento_e_sem_caixa(self):
        equipe = [drv("José Neto"), drv("Ana Costa")]
        for texto in ["jose neto", "JOSÉ NETO", "José neto"]:
            with self.subTest(texto=texto):
                self.assertEqual(fb._achar_motorista(equipe, texto)[0]["name"], "José Neto")

    def test_parte_do_nome_unica(self):
        d, erro = fb._achar_motorista(EQUIPE, "costa")
        self.assertEqual((d["name"], erro), ("Ana Costa", None))

    def test_ambiguo_nao_escolhe_ninguem(self):
        d, erro = fb._achar_motorista(EQUIPE, "pedro")
        self.assertIsNone(d)
        self.assertIn("Pedro Lima", erro)
        self.assertIn("Pedro Alves", erro)
        self.assertIn("Especifique melhor", erro)

    def test_nao_encontrado(self):
        d, erro = fb._achar_motorista(EQUIPE, "Zeca")
        self.assertIsNone(d)
        self.assertIn("não encontrado", erro)

    def test_todas_as_partes_precisam_estar_no_mesmo_nome(self):
        # "ana" está em Ana Costa e "lima" em Pedro Lima, mas nenhum nome tem as duas
        d, erro = fb._achar_motorista(EQUIPE, "ana lima")
        self.assertIsNone(d)
        self.assertIn("não encontrado", erro)

    def test_nome_exato_vence_correspondencia_parcial(self):
        equipe = [{"id": "driver_a", "name": "Ana"}, {"id": "driver_b", "name": "Ana Maria"}]
        d, erro = fb._achar_motorista(equipe, "ana")
        self.assertEqual((d["id"], erro), ("driver_a", None))


class Base(unittest.TestCase):
    def leituras(self, pedidos, motoristas=None):
        for alvo, kw in [("_all_orders", {"return_value": pedidos}),
                         ("_get", {"return_value": motoristas if motoristas is not None else EQUIPE}),
                         ("_items", {"side_effect": lambda r: r}),
                         ("_summarize_order", {"side_effect": resumo})]:
            p = patch.object(fb, alvo, **kw)
            p.start()
            self.addCleanup(p.stop)


class TestPreparar(Base):
    def setUp(self):
        p = patch.object(fb, "_send", side_effect=AssertionError("preparar não pode gravar"), create=True)
        p.start()
        self.addCleanup(p.stop)

    def test_pedido_livre_e_motorista_livre(self):
        self.leituras([pedido("LO100001")])
        r = fb.preparar_atribuicao("LO100001", "Beatriz")
        self.assertEqual(r["pedido_id"], "order_lo100001")
        self.assertEqual(r["pedido_codigo"], "LO100001")
        self.assertEqual(r["motorista_id"], "driver_beatriz")
        self.assertEqual(r["motorista_nome"], "Beatriz Rocha")
        self.assertTrue(r["despachar"])
        self.assertEqual(r["avisos"], [])
        self.assertEqual(r["pedido"]["codigo"], "LO100001")

    def test_despachar_depende_do_status_sem_importar_a_caixa(self):
        for status, esperado in [("created", True), ("CREATED", True), ("dispatched", False), ("Dispatched", False)]:
            with self.subTest(status=status):
                self.leituras([pedido("LO100001", status=status)])
                self.assertEqual(fb.preparar_atribuicao("LO100001", "Beatriz")["despachar"], esperado)

    def test_motorista_ocupado_gera_aviso_com_contagem_e_codigos(self):
        ana = drv("Ana Costa")
        self.leituras([pedido("LO100001"),
                       pedido("LO100002", "dispatched", ana),
                       pedido("LO100003", "started", ana)])
        avisos = fb.preparar_atribuicao("LO100001", "Ana")["avisos"]
        self.assertEqual(len(avisos), 1)
        self.assertIn("2 pedido(s) ativo(s)", avisos[0])
        self.assertIn("LO100002", avisos[0])
        self.assertIn("LO100003", avisos[0])

    def test_ocupado_so_conta_pedidos_ativos(self):
        ana = drv("Ana Costa")
        self.leituras([pedido("LO100001"),
                       pedido("LO100002", "completed", ana),
                       pedido("LO100003", "canceled", ana)])
        self.assertEqual(fb.preparar_atribuicao("LO100001", "Ana")["avisos"], [])

    def test_ocupado_reconhecido_so_pelo_nome(self):
        # no pedido, o motorista vem sem id: só o nome identifica
        self.leituras([pedido("LO100001"), pedido("LO100002", "dispatched", {"name": "Ana Costa"})])
        self.assertEqual(len(fb.preparar_atribuicao("LO100001", "Ana")["avisos"]), 1)

    def test_ocupado_com_outro_motorista_nao_conta(self):
        self.leituras([pedido("LO100001"), pedido("LO100002", "dispatched", drv("Beatriz Rocha"))])
        self.assertEqual(fb.preparar_atribuicao("LO100001", "Ana")["avisos"], [])

    def test_pedido_inexistente(self):
        self.leituras([pedido("LO100001")])
        self.assertIn("não encontrado", fb.preparar_atribuicao("LO999999", "Ana")["erro"])

    def test_pedido_encerrado(self):
        for status in ["completed", "canceled"]:
            with self.subTest(status=status):
                self.leituras([pedido("LO100001", status)])
                r = fb.preparar_atribuicao("LO100001", "Ana")
                self.assertIn(status, r["erro"])
                self.assertNotIn("pedido_id", r)

    def test_pedido_que_ja_tem_motorista(self):
        self.leituras([pedido("LO100001", "dispatched", drv("Ana Costa"))])
        r = fb.preparar_atribuicao("LO100001", "Beatriz")
        self.assertIn("já tem o motorista Ana Costa", r["erro"])
        self.assertNotIn("pedido_id", r)

    def test_motorista_inexistente_ou_ambiguo(self):
        self.leituras([pedido("LO100001")])
        self.assertIn("não encontrado", fb.preparar_atribuicao("LO100001", "Zeca")["erro"])
        self.assertIn("Mais de um motorista", fb.preparar_atribuicao("LO100001", "Pedro")["erro"])


class TestPedidoPorId(Base):
    def test_acha_o_certo_ou_devolve_none(self):
        self.leituras([pedido("LO100001"), pedido("LO100002")])
        self.assertEqual(fb._pedido_por_id("order_lo100002")["internal_id"], "LO100002")
        self.assertIsNone(fb._pedido_por_id("order_inexistente"))


class TestAtribuir(Base):
    def escrita(self, leituras, send=None):
        """leituras: o que cada releitura de pedidos devolve, em ordem."""
        self.leituras([])
        p = patch.object(fb, "_all_orders", side_effect=leituras)
        p.start()
        self.addCleanup(p.stop)
        self.send = send or MagicMock(return_value={"ok": True})
        p = patch.object(fb, "_send", self.send, create=True)
        p.start()
        self.addCleanup(p.stop)

    def test_atribui_e_despacha(self):
        ana = drv("Ana Costa")
        self.escrita([[pedido("LO100001", "created", ana)], [pedido("LO100001", "dispatched", ana)]])
        r = fb.atribuir_motorista("order_lo100001", "driver_ana", "Ana Costa", True)
        self.assertEqual(self.send.call_args_list, [
            call("PUT", "/orders/order_lo100001", {"driver": "driver_ana"}),
            call("PATCH", "/orders/order_lo100001/dispatch")])
        self.assertEqual(r["pedido"], {"codigo": "LO100001", "status": "dispatched"})
        self.assertEqual(r["avisos"], [])

    def test_despachar_false_so_atribui(self):
        ana = drv("Ana Costa")
        self.escrita([[pedido("LO100001", "created", ana)]])
        r = fb.atribuir_motorista("order_lo100001", "driver_ana", "Ana Costa", False)
        self.assertEqual(self.send.call_args_list,
                         [call("PUT", "/orders/order_lo100001", {"driver": "driver_ana"})])
        self.assertEqual(r["pedido"]["status"], "created")

    def test_pedido_que_nao_esta_created_nao_e_despachado(self):
        ana = drv("Ana Costa")
        self.escrita([[pedido("LO100001", "dispatched", ana)]])
        fb.atribuir_motorista("order_lo100001", "driver_ana", "Ana Costa", True)
        self.assertEqual(self.send.call_count, 1)

    def test_grava_o_id_do_motorista_nao_o_nome(self):
        ana = drv("Ana Costa")
        self.escrita([[pedido("LO100001", "dispatched", ana)]])
        fb.atribuir_motorista("order_lo100001", "driver_ana", "Ana Costa", False)
        self.assertEqual(self.send.call_args_list[0].args[2], {"driver": "driver_ana"})

    def test_confere_na_releitura_motorista_ausente(self):
        self.escrita([[pedido("LO100001", "created", None)]])
        with self.assertRaises(fb.FleetbaseError) as cm:
            fb.atribuir_motorista("order_lo100001", "driver_ana", "Ana Costa", True)
        self.assertIn("não ficou atribuído", str(cm.exception))
        self.assertEqual(self.send.call_count, 1)    # nada de despacho depois de uma atribuição falha

    def test_confere_na_releitura_outro_motorista(self):
        self.escrita([[pedido("LO100001", "created", drv("Beatriz Rocha"))]])
        with self.assertRaises(fb.FleetbaseError):
            fb.atribuir_motorista("order_lo100001", "driver_ana", "Ana Costa", True)
        self.assertEqual(self.send.call_count, 1)

    def test_pedido_sumiu_na_releitura(self):
        self.escrita([[]])
        with self.assertRaises(fb.FleetbaseError):
            fb.atribuir_motorista("order_lo100001", "driver_ana", "Ana Costa", True)

    def test_aceita_pelo_nome_quando_o_id_difere(self):
        self.escrita([[pedido("LO100001", "dispatched", {"id": "outro_id", "name": "Ana Costa"})]])
        fb.atribuir_motorista("order_lo100001", "driver_ana", "Ana Costa", False)

    def test_sem_nome_so_vale_o_id(self):
        self.escrita([[pedido("LO100001", "dispatched", {"id": "outro_id", "name": "Ana Costa"})]])
        with self.assertRaises(fb.FleetbaseError):
            fb.atribuir_motorista("order_lo100001", "driver_ana", None, False)

    def test_falha_no_despacho_vira_aviso_e_nao_erro(self):
        ana = drv("Ana Costa")
        send = MagicMock(side_effect=[{"ok": True}, fb.FleetbaseError("timeout")])
        self.escrita([[pedido("LO100001", "created", ana)]], send)
        r = fb.atribuir_motorista("order_lo100001", "driver_ana", "Ana Costa", True)
        self.assertEqual(r["avisos"], ["Motorista atribuído, mas o despacho falhou: timeout"])
        self.assertEqual(r["pedido"]["status"], "created")

    def test_falha_ao_gravar_propaga_o_erro(self):
        send = MagicMock(side_effect=fb.FleetbaseError("HTTP 500"))
        self.escrita([], send)
        with self.assertRaises(fb.FleetbaseError):
            fb.atribuir_motorista("order_lo100001", "driver_ana", "Ana Costa", True)
        self.assertEqual(send.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
