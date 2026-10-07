"""Testes de tools.py: fluxo de escrita (proposta -> confirmação), validação das entradas
que vêm do modelo e invariantes de segurança.

Não usa Azure nem o Fleetbase: tudo que vai para fleetbase_client é simulado (mock).
    python test_tools.py
"""
import unittest
from unittest.mock import patch

import fleetbase_client as fb
import tools

NOMES_FB = ["resumo_do_dia", "listar_pedidos", "listar_atrasados", "consultar_pedido",
            "listar_motoristas", "explicar_atraso", "sugerir_motorista",
            "preparar_atribuicao", "atribuir_motorista"]

FERRAMENTAS_ESPERADAS = {"resumo_do_dia", "listar_pedidos", "listar_atrasados", "consultar_pedido",
                         "listar_motoristas", "explicar_atraso", "sugerir_motorista",
                         "propor_atribuicao"}

LEITURA = ["resumo_do_dia", "listar_pedidos", "listar_atrasados", "consultar_pedido",
           "listar_motoristas", "explicar_atraso", "sugerir_motorista"]


def rascunho(sufixo=""):
    return {"pedido_id": "order_real" + sufixo, "pedido_codigo": "LO100001" + sufixo,
            "motorista_id": "driver_real" + sufixo, "motorista_nome": "Ana Costa" + sufixo,
            "despachar": True, "avisos": ["Ana Costa já tem 1 pedido ativo: LO040969."]}


class Base(unittest.TestCase):
    def setUp(self):
        tools.PROPOSTAS.clear()
        self.addCleanup(tools.PROPOSTAS.clear)
        self.m = {}
        for nome in NOMES_FB:
            p = patch.object(fb, nome, return_value={"ok": True})
            self.m[nome] = p.start()
            self.addCleanup(p.stop)
        self.m["preparar_atribuicao"].return_value = rascunho()
        self.m["atribuir_motorista"].return_value = {"status": "dispatched"}
        # qualquer escrita direta na API do Fleetbase derruba o teste
        p = patch.object(fb, "_send", side_effect=AssertionError("escrita direta proibida"), create=True)
        p.start()
        self.addCleanup(p.stop)

    def propor(self, pedido="LO100001", motorista="Ana"):
        r = tools.executar_ferramenta("propor_atribuicao", {"pedido": pedido, "motorista": motorista})
        return r["proposta_id"]


class TestPropor(Base):
    def test_cria_proposta_sem_gravar(self):
        r = tools.executar_ferramenta("propor_atribuicao", {"pedido": "LO100001", "motorista": "Ana"})
        self.assertEqual(r["status"], "aguardando_confirmacao_do_usuario")
        self.assertIn("NADA foi executado", r["instrucao"])
        self.assertEqual(r["pedido"], "LO100001")
        self.assertEqual(r["motorista"], "Ana Costa")
        self.assertEqual(len(r["avisos"]), 1)
        self.assertIn(r["proposta_id"], tools.PROPOSTAS)
        self.m["atribuir_motorista"].assert_not_called()

    def test_rascunho_guarda_os_ids_reais(self):
        pid = self.propor()
        guardado = tools.obter_proposta(pid)
        self.assertEqual(guardado["pedido_id"], "order_real")
        self.assertEqual(guardado["motorista_id"], "driver_real")

    def test_entradas_invalidas_nao_chegam_ao_fleetbase(self):
        invalidas = [{}, {"pedido": "", "motorista": "Ana"}, {"pedido": "   ", "motorista": "Ana"},
                     {"pedido": "LO1"}, {"pedido": "LO1", "motorista": ""},
                     {"pedido": "LO1", "motorista": "  "}, {"pedido": 123, "motorista": "Ana"},
                     {"pedido": "LO1", "motorista": ["Ana"]}, {"pedido": None, "motorista": None}]
        for args in invalidas:
            with self.subTest(args=args):
                self.assertIn("erro", tools.executar_ferramenta("propor_atribuicao", args))
        self.m["preparar_atribuicao"].assert_not_called()
        self.assertEqual(tools.PROPOSTAS, {})

    def test_trunca_entradas_longas(self):
        tools.executar_ferramenta("propor_atribuicao", {"pedido": "A" * 200, "motorista": "B" * 200})
        self.m["preparar_atribuicao"].assert_called_once_with("A" * 60, "B" * 80)

    def test_erro_do_fleetbase_nao_cria_proposta(self):
        self.m["preparar_atribuicao"].return_value = {"erro": "O pedido já tem motorista."}
        r = tools.executar_ferramenta("propor_atribuicao", {"pedido": "LO1", "motorista": "Ana"})
        self.assertEqual(r, {"erro": "O pedido já tem motorista."})
        self.assertEqual(tools.PROPOSTAS, {})

    def test_excecao_do_fleetbase_vira_erro(self):
        self.m["preparar_atribuicao"].side_effect = fb.FleetbaseError("Fleetbase fora do ar")
        r = tools.executar_ferramenta("propor_atribuicao", {"pedido": "LO1", "motorista": "Ana"})
        self.assertEqual(r, {"erro": "Fleetbase fora do ar"})
        self.assertEqual(tools.PROPOSTAS, {})


class TestConfirmar(Base):
    def test_grava_com_os_ids_do_rascunho(self):
        pid = self.propor()
        res = tools.confirmar_proposta(pid)
        self.assertTrue(res["ok"])
        self.m["atribuir_motorista"].assert_called_once_with("order_real", "driver_real", "Ana Costa", True)
        self.assertEqual(res["pedido_codigo"], "LO100001")
        self.assertEqual(res["motorista_nome"], "Ana Costa")
        self.assertEqual(res["status"], "dispatched")

    def test_uso_unico(self):
        pid = self.propor()
        self.assertTrue(tools.confirmar_proposta(pid)["ok"])
        self.assertFalse(tools.confirmar_proposta(pid)["ok"])
        self.assertEqual(self.m["atribuir_motorista"].call_count, 1)

    def test_id_desconhecido_nao_grava(self):
        for pid in ["naoexiste", "", None]:
            with self.subTest(pid=pid):
                self.assertFalse(tools.confirmar_proposta(pid)["ok"])
        self.m["atribuir_motorista"].assert_not_called()

    def test_vale_ate_10_minutos_exatos(self):
        with patch.object(tools.time, "time", return_value=1000.0):
            pid = self.propor()
        with patch.object(tools.time, "time", return_value=1600.0):
            self.assertIsNotNone(tools.obter_proposta(pid))
            self.assertTrue(tools.confirmar_proposta(pid)["ok"])

    def test_expira_depois_de_10_minutos(self):
        with patch.object(tools.time, "time", return_value=1000.0):
            pid = self.propor()
        with patch.object(tools.time, "time", return_value=1600.5):
            self.assertIsNone(tools.obter_proposta(pid))
            res = tools.confirmar_proposta(pid)
        self.assertFalse(res["ok"])
        self.assertIn("expirada", res["erro"])
        self.m["atribuir_motorista"].assert_not_called()

    def test_confirmar_expirada_direto_sem_passar_por_obter(self):
        with patch.object(tools.time, "time", return_value=1000.0):
            pid = self.propor()
        with patch.object(tools.time, "time", return_value=5000.0):
            self.assertFalse(tools.confirmar_proposta(pid)["ok"])
        self.m["atribuir_motorista"].assert_not_called()

    def test_falha_ao_gravar_consome_a_proposta(self):
        self.m["atribuir_motorista"].side_effect = fb.FleetbaseError("conflito")
        pid = self.propor()
        res = tools.confirmar_proposta(pid)
        self.assertEqual(res, {"ok": False, "erro": "conflito"})
        self.assertFalse(tools.confirmar_proposta(pid)["ok"])
        self.assertEqual(self.m["atribuir_motorista"].call_count, 1)

    def test_excecao_inesperada_nao_vaza_detalhes(self):
        self.m["atribuir_motorista"].side_effect = ValueError("senha=123")
        res = tools.confirmar_proposta(self.propor())
        self.assertFalse(res["ok"])
        self.assertIn("ValueError", res["erro"])
        self.assertNotIn("senha", res["erro"])

    def test_cancelar_impede_a_gravacao(self):
        pid = self.propor()
        tools.cancelar_proposta(pid)
        tools.cancelar_proposta("id-que-nao-existe")   # não pode levantar erro
        self.assertFalse(tools.confirmar_proposta(pid)["ok"])
        self.m["atribuir_motorista"].assert_not_called()

    def test_propostas_sao_independentes(self):
        self.m["preparar_atribuicao"].side_effect = [rascunho("A"), rascunho("B")]
        pa, pb = self.propor(), self.propor()
        self.assertNotEqual(pa, pb)
        tools.confirmar_proposta(pb)
        self.m["atribuir_motorista"].assert_called_once_with("order_realB", "driver_realB", "Ana CostaB", True)
        self.assertIsNotNone(tools.obter_proposta(pa))


class TestExecutar(Base):
    def test_ferramenta_desconhecida(self):
        self.assertIn("desconhecida", tools.executar_ferramenta("apagar_tudo", {})["erro"])

    def test_confirmar_e_atribuir_nao_sao_ferramentas_do_modelo(self):
        for nome in ["confirmar_proposta", "atribuir_motorista"]:
            with self.subTest(nome=nome):
                self.assertIn("desconhecida", tools.executar_ferramenta(nome, {"proposta_id": "x"})["erro"])
        self.m["atribuir_motorista"].assert_not_called()

    def test_status_invalido_nao_chama_o_fleetbase(self):
        for status in ["foo", 5, ["created"], ""]:
            with self.subTest(status=status):
                self.assertIn("erro", tools.executar_ferramenta("listar_pedidos", {"status": status}))
        self.m["listar_pedidos"].assert_not_called()

    def test_status_todos_vira_sem_filtro(self):
        tools.executar_ferramenta("listar_pedidos", {"status": "todos"})
        self.m["listar_pedidos"].assert_called_with(None)
        tools.executar_ferramenta("listar_pedidos", {})
        self.m["listar_pedidos"].assert_called_with(None)
        tools.executar_ferramenta("listar_pedidos", {"status": "created"})
        self.m["listar_pedidos"].assert_called_with("created")

    def test_consultar_pedido_valida_e_trunca(self):
        for ident in [None, "", "   ", 42]:
            self.assertIn("erro", tools.executar_ferramenta("consultar_pedido", {"identificador": ident}))
        self.m["consultar_pedido"].assert_not_called()
        tools.executar_ferramenta("consultar_pedido", {"identificador": "X" * 100})
        self.m["consultar_pedido"].assert_called_once_with("X" * 60)

    def test_explicar_e_sugerir_chamam_a_funcao_certa(self):
        tools.executar_ferramenta("explicar_atraso", {"pedido": "LO1"})
        self.m["explicar_atraso"].assert_called_once_with("LO1")
        self.m["sugerir_motorista"].assert_not_called()
        tools.executar_ferramenta("sugerir_motorista", {"pedido": "Y" * 100})
        self.m["sugerir_motorista"].assert_called_once_with("Y" * 60)
        self.assertEqual(self.m["explicar_atraso"].call_count, 1)

    def test_explicar_e_sugerir_validam_o_pedido(self):
        for nome in ["explicar_atraso", "sugerir_motorista"]:
            for pedido in [None, "", "  ", 7]:
                with self.subTest(nome=nome, pedido=pedido):
                    self.assertIn("erro", tools.executar_ferramenta(nome, {"pedido": pedido}))
            self.m[nome].assert_not_called()

    def test_listar_motoristas_converte_para_booleano(self):
        tools.executar_ferramenta("listar_motoristas", {"apenas_livres": True})
        self.m["listar_motoristas"].assert_called_with(True)
        tools.executar_ferramenta("listar_motoristas", {})
        self.m["listar_motoristas"].assert_called_with(False)

    def test_limite_de_itens(self):
        self.m["listar_atrasados"].return_value = list(range(30))
        self.assertEqual(tools.executar_ferramenta("listar_atrasados", {}), list(range(30)))
        self.m["listar_atrasados"].return_value = list(range(31))
        r = tools.executar_ferramenta("listar_atrasados", {})
        self.assertEqual(len(r["itens"]), 30)
        self.assertIn("30 de 31", r["aviso"])

    def test_excecoes_viram_erro_sem_vazar_detalhes(self):
        self.m["resumo_do_dia"].side_effect = fb.FleetbaseError("sem conexão")
        self.assertEqual(tools.executar_ferramenta("resumo_do_dia", {}), {"erro": "sem conexão"})
        self.m["resumo_do_dia"].side_effect = KeyError("segredo")
        self.assertEqual(tools.executar_ferramenta("resumo_do_dia", {}),
                         {"erro": "Falha inesperada em resumo_do_dia: KeyError"})

    def test_ferramentas_de_leitura_e_proposta_nunca_gravam(self):
        for nome in LEITURA:
            tools.executar_ferramenta(nome, self._args_validos(nome))
        self.propor()
        self.m["atribuir_motorista"].assert_not_called()

    def _args_validos(self, nome):
        ferramenta = next(t for t in tools.FERRAMENTAS if t.name == nome)
        args = {}
        for campo, spec in ferramenta.parameters["properties"].items():
            args[campo] = (spec["enum"][0] if "enum" in spec
                           else False if spec["type"] == "boolean" else "X")
        return args


class TestRegistro(Base):
    def test_nomes_expostos_ao_modelo(self):
        self.assertEqual({t.name for t in tools.FERRAMENTAS}, FERRAMENTAS_ESPERADAS)

    def test_todas_estritas_e_sem_campos_extras(self):
        for t in tools.FERRAMENTAS:
            with self.subTest(ferramenta=t.name):
                self.assertTrue(t.strict)
                self.assertIs(t.parameters["additionalProperties"], False)
                self.assertEqual(set(t.parameters["required"]), set(t.parameters["properties"]))

    def test_toda_ferramenta_declarada_tem_tratamento(self):
        for t in tools.FERRAMENTAS:
            with self.subTest(ferramenta=t.name):
                args = TestExecutar._args_validos(self, t.name)
                r = tools.executar_ferramenta(t.name, args)
                self.assertNotIn("desconhecida", str(r))


if __name__ == "__main__":
    unittest.main(verbosity=1)
