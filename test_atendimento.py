"""Testes de tools_atendimento.py: privilégio mínimo e a trava "só consulta o código que o
cliente escreveu".

Não usa Azure nem o Fleetbase. A normalização de código é simulada para que estes testes
dependam só da lógica de tools_atendimento.py.
    python test_atendimento.py
"""
import unicodedata
import unittest
from unittest.mock import patch

import fleetbase_client as fb
import tools_atendimento as ta

CODIGO = "LOG1456833839BR"
FERRAMENTAS_DO_DESPACHANTE = ["resumo_do_dia", "listar_pedidos", "listar_atrasados", "consultar_pedido",
                              "listar_motoristas", "explicar_atraso", "sugerir_motorista",
                              "propor_atribuicao", "confirmar_proposta", "atribuir_motorista"]


def norm(texto):
    """Minúsculas, sem acento, só letras e dígitos (o que fb.normalizar_codigo faz)."""
    t = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in t if c.isascii() and c.isalnum())


TEXTOS = [norm(f"Oi, meu pedido é o {CODIGO}, onde ele está?")]


class Base(unittest.TestCase):
    def setUp(self):
        for alvo, kw in [("normalizar_codigo", {"side_effect": norm}),
                         ("consultar_rastreio_publico", {"return_value": {"situacao": "em rota"}})]:
            p = patch.object(fb, alvo, **kw)
            mock = p.start()
            self.addCleanup(p.stop)
            if alvo == "consultar_rastreio_publico":
                self.consulta = mock

    def chamar(self, codigo, textos=TEXTOS, nome="consultar_rastreio"):
        return ta.executar_ferramenta_atendimento(nome, {"codigo": codigo}, textos)


class TestPrivilegioMinimo(Base):
    def test_uma_unica_ferramenta(self):
        self.assertEqual([t.name for t in ta.FERRAMENTAS_ATENDIMENTO], ["consultar_rastreio"])

    def test_esquema_estrito(self):
        t = ta.FERRAMENTAS_ATENDIMENTO[0]
        self.assertTrue(t.strict)
        self.assertIs(t.parameters["additionalProperties"], False)
        self.assertEqual(t.parameters["required"], ["codigo"])
        self.assertEqual(list(t.parameters["properties"]), ["codigo"])

    def test_ferramentas_do_despachante_nao_existem_aqui(self):
        for nome in FERRAMENTAS_DO_DESPACHANTE:
            with self.subTest(nome=nome):
                r = ta.executar_ferramenta_atendimento(nome, {"codigo": CODIGO, "pedido": CODIGO}, TEXTOS)
                self.assertIn("desconhecida", r["erro"])
        self.consulta.assert_not_called()


class TestValidacao(Base):
    def test_codigo_invalido_nao_chega_ao_fleetbase(self):
        for textos in (None, TEXTOS):
            for codigo in [None, "", "   ", 123, ["x"]]:
                with self.subTest(codigo=codigo, textos=textos):
                    self.assertIn("erro", self.chamar(codigo, textos))
        self.consulta.assert_not_called()

    def test_argumento_ausente(self):
        self.assertIn("erro", ta.executar_ferramenta_atendimento("consultar_rastreio", {}, TEXTOS))
        self.consulta.assert_not_called()

    def test_codigo_longo_e_truncado_em_40(self):
        longo = "A" * 100
        self.chamar(longo, [norm(longo)])
        self.consulta.assert_called_once_with("A" * 40)


class TestTrava(Base):
    def test_codigo_que_o_cliente_escreveu_passa(self):
        r = self.chamar(CODIGO)
        self.assertEqual(r, {"situacao": "em rota"})
        self.consulta.assert_called_once_with(CODIGO)

    def test_formatos_diferentes_do_mesmo_codigo_passam(self):
        for variante in ["log1456833839br", "LOG-1456 833839 BR", " Log 1456833839br "]:
            with self.subTest(variante=variante):
                self.consulta.reset_mock()
                self.assertNotIn("erro", self.chamar(variante))
                self.consulta.assert_called_once_with(variante)

    def test_codigo_alterado_pelo_modelo_e_recusado(self):
        for alterado in ["LOG1456833838BR", "LOG1456833839BX", "LOG2456833839BR", "LOG1456833839BRX",
                         "XLOG1456833839BR"]:
            with self.subTest(alterado=alterado):
                r = self.chamar(alterado)
                self.assertIn("não corresponde", r["erro"])
        self.consulta.assert_not_called()

    def test_limitacao_conhecida_pedaco_do_codigo_passa_pela_trava(self):
        # A trava procura o código DENTRO do texto do cliente, então um PEDAÇO do que ele escreveu
        # passa. Só é inofensivo porque consultar_rastreio_publico exige o código de rastreio completo
        # e exato. Se esse teste quebrar, a trava ficou mais rígida (ótimo) ou a busca mudou: reavalie.
        self.assertNotIn("erro", self.chamar("LOG1456833839"))
        self.consulta.assert_called_once_with("LOG1456833839")

    def test_codigo_que_o_cliente_nunca_escreveu_e_recusado(self):
        r = self.chamar("LOG9999999999BR")
        self.assertIn("não corresponde", r["erro"])
        self.consulta.assert_not_called()

    def test_cliente_que_ainda_nao_escreveu_nada_nao_consulta_nada(self):
        # lista vazia NÃO é "sem trava": tem de recusar tudo
        r = self.chamar(CODIGO, textos=[])
        self.assertIn("não corresponde", r["erro"])
        self.consulta.assert_not_called()

    def test_codigo_em_qualquer_mensagem_da_conversa(self):
        textos = [norm("Olá, preciso de ajuda"), norm(f"é o {CODIGO}"), norm("obrigado")]
        self.assertNotIn("erro", self.chamar(CODIGO, textos))
        self.consulta.assert_called_once_with(CODIGO)

    def test_texto_do_cliente_mais_longo_que_o_codigo(self):
        # o código é buscado DENTRO do que o cliente escreveu, não o contrário
        self.assertNotIn("erro", self.chamar(CODIGO, [norm("x" * 200 + CODIGO + "y" * 200)]))

    def test_sem_textos_do_cliente_a_trava_fica_desligada(self):
        # contrato documentado: None = sem restrição. Quem chama (atendimento.py) PRECISA passar a lista.
        self.assertNotIn("erro", self.chamar("QUALQUERCODIGO1", textos=None))
        self.consulta.assert_called_once_with("QUALQUERCODIGO1")


class TestErros(Base):
    def test_resultado_do_fleetbase_passa_sem_alteracao(self):
        esperado = {"situacao": "atrasado", "bairro": "Moema", "atrasado": True}
        self.consulta.return_value = esperado
        self.assertEqual(self.chamar(CODIGO), esperado)

    def test_erro_do_fleetbase_nao_vaza_detalhes(self):
        self.consulta.side_effect = fb.FleetbaseError("Falha em http://10.0.0.5:8000 com a chave flb_live_X")
        r = self.chamar(CODIGO)
        self.assertEqual(r, {"erro": "Não consegui consultar o pedido agora. Tente novamente em instantes."})

    def test_excecao_inesperada_nao_vaza_detalhes(self):
        self.consulta.side_effect = KeyError("segredo")
        r = self.chamar(CODIGO)
        self.assertEqual(r, {"erro": "Falha inesperada: KeyError"})


if __name__ == "__main__":
    unittest.main(verbosity=1)
