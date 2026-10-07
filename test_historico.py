"""Testes de Atendente.perguntar (atendimento.py): o histórico que alimenta a trava
"só consulta o código que o cliente escreveu".

Não usa Azure: o objeto é criado sem rodar __init__ e executar_turno é simulado; o "modelo"
tenta consultar os códigos que o teste manda. fb.consultar_rastreio_publico também é simulado.
    python test_historico.py
"""
import unittest
from unittest.mock import patch

import atendimento
import fleetbase_client as fb

CODIGO = "Gó8881740690BR"
OUTRO = "LOG9999999999BR"


class Base(unittest.TestCase):
    def setUp(self):
        self.consulta = patch.object(fb, "consultar_rastreio_publico",
                                     return_value={"situacao": "em rota"}).start()
        self.addCleanup(patch.stopall)
        self.tentar, self.resposta, self.resultados, self.textos_recebidos = [], "ok", [], []

        def executar_turno_falso(openai, cid, texto, executor, fontes_out=None):
            self.textos_recebidos.append(texto)
            self.resultados = [executor("consultar_rastreio", {"codigo": c}) for c in self.tentar]
            if fontes_out is not None:
                fontes_out.append("02_politica_de_atrasos.md")
            return self.resposta, [{"nome": "consultar_rastreio"}]

        patch.object(atendimento, "executar_turno", side_effect=executar_turno_falso).start()
        self.bot = Atendente_sem_init()

    def turno(self, texto, tentar=(), cid=None, resposta="ok"):
        """Uma mensagem do cliente; o 'modelo' tenta consultar os códigos de `tentar`."""
        self.tentar, self.resposta = list(tentar), resposta
        saida = self.bot.perguntar(texto, conversation_id=cid)
        return saida, self.resultados


def Atendente_sem_init():
    bot = atendimento.Atendente.__new__(atendimento.Atendente)
    bot._textos = {}
    bot.openai = object()
    bot.conversation_id = "conv_padrao"
    return bot


def recusado(r):
    return "não corresponde" in r.get("erro", "")


class TestTrava(Base):
    def test_codigo_escrito_na_propria_mensagem_pode_ser_consultado(self):
        _, res = self.turno(f"Meu pedido é {CODIGO}", tentar=[CODIGO])
        self.assertEqual(res, [{"situacao": "em rota"}])
        self.consulta.assert_called_once_with(CODIGO)

    def test_codigo_que_o_cliente_nao_escreveu_e_recusado(self):
        _, res = self.turno("Onde está meu pedido?", tentar=[OUTRO])
        self.assertTrue(recusado(res[0]))
        self.consulta.assert_not_called()

    def test_codigo_de_mensagem_anterior_continua_valendo(self):
        self.turno(f"Meu pedido é {CODIGO}")
        _, res = self.turno("obrigado, e então?", tentar=[CODIGO])
        self.assertFalse(recusado(res[0]))

    def test_o_que_o_cliente_escreve_e_normalizado(self):
        _, res = self.turno("meu código: Gó-8881 740690 br", tentar=["GÓ8881740690BR"])
        self.assertFalse(recusado(res[0]))

    def test_resposta_do_agente_nao_autoriza_codigo(self):
        # o agente escreveu um código na resposta; isso NÃO vale como se o cliente o tivesse escrito
        self.turno("oi", resposta=f"Seu pedido {OUTRO} está em rota")
        _, res = self.turno("e agora?", tentar=[OUTRO])
        self.assertTrue(recusado(res[0]))
        self.consulta.assert_not_called()

    def test_conversas_sao_isoladas(self):
        self.turno(f"Meu pedido é {CODIGO}", cid="conv_A")
        _, res = self.turno("consulte para mim", tentar=[CODIGO], cid="conv_B")
        self.assertTrue(recusado(res[0]))
        _, res = self.turno("consulte de novo", tentar=[CODIGO], cid="conv_A")
        self.assertFalse(recusado(res[0]))

    def test_conversa_padrao_quando_nao_se_informa_o_id(self):
        self.turno(f"Meu pedido é {CODIGO}")
        _, res = self.turno("e aí?", tentar=[CODIGO], cid="conv_padrao")
        self.assertFalse(recusado(res[0]))


class TestJanela(Base):
    def test_lembra_das_ultimas_50_mensagens(self):
        self.turno(f"Meu pedido é {CODIGO}")                       # mensagem 1
        for i in range(48):                                        # mensagens 2 a 49
            self.turno(f"conversa {i}")
        _, res = self.turno("consulte", tentar=[CODIGO])           # mensagem 50: a 1ª ainda está na janela
        self.assertFalse(recusado(res[0]))
        _, res = self.turno("consulte de novo", tentar=[CODIGO])   # mensagem 51: a 1ª saiu da janela
        self.assertTrue(recusado(res[0]))

    def test_historico_nunca_passa_de_50(self):
        for i in range(120):
            self.turno(f"mensagem {i}")
        self.assertEqual(len(self.bot._textos["conv_padrao"]), 50)


class TestEntrada(Base):
    def test_devolve_resposta_chamadas_e_fontes(self):
        (resposta, chamadas, fontes), _ = self.turno("oi", resposta="Olá!")
        self.assertEqual(resposta, "Olá!")
        self.assertEqual(chamadas, [{"nome": "consultar_rastreio"}])
        self.assertEqual(fontes, ["02_politica_de_atrasos.md"])

    def test_bytes_invalidos_do_terminal_sao_descartados(self):
        self.turno("oi \ud800 meu código é " + CODIGO, tentar=[CODIGO])
        self.assertEqual(self.textos_recebidos, ["oi  meu código é " + CODIGO])
        self.assertFalse(recusado(self.resultados[0]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
