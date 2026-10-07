"""Testes da camada de resiliência: falhas do Azure/Foundry em agent.py e atendimento.py.

Não usa Azure nem o Fleetbase: o cliente do modelo é simulado, as falhas são fabricadas e as
ferramentas chamadas são inexistentes (respondem com erro, sem tocar em nenhuma API).
    python test_resiliencia.py
"""
import inspect
import json
import logging
import re
import unittest
from types import SimpleNamespace as NS

import agent
import atendimento

CLASSES = [agent.Despachante, atendimento.Atendente]


def setUpModule():
    logging.disable(logging.CRITICAL)      # o log de falhas é esperado nestes testes: sem ruído


def tearDownModule():
    logging.disable(logging.NOTSET)


def chamada(nome="ferramenta_inexistente", args="{}", call_id="call_1"):
    return NS(type="function_call", name=nome, arguments=args, call_id=call_id)


def com_chamadas(*itens):
    return NS(output=list(itens), output_text="")


def texto(t="Pronto."):
    return NS(output=[NS(type="message", content=[])], output_text=t)


def erro(nome, status=None):
    """Exceção com o nome de classe dado (como as do SDK) e, se for o caso, status HTTP."""
    e = type(nome, (Exception,), {})("detalhe interno 10.0.0.5 com a chave flb_live_X")
    if status is not None:
        e.status_code = status
    return e


class Responses:
    """Devolve, em ordem, as respostas do roteiro (ou levanta as exceções dele). Esgotado, repete a última."""

    def __init__(self, roteiro):
        self.roteiro, self.pedidos, self.conversas = list(roteiro), [], []

    def create(self, conversation, input):
        self.conversas.append(conversation)
        self.pedidos.append(input)
        item = self.roteiro.pop(0) if len(self.roteiro) > 1 else self.roteiro[0]
        if isinstance(item, BaseException):
            raise item
        return item


class Conversas:
    def __init__(self):
        self.criadas, self.falhar = 0, False

    def create(self):
        if self.falhar:
            raise erro("APIConnectionError")
        self.criadas += 1
        return NS(id=f"conv_nova_{self.criadas}")


def cliente(*roteiro):
    return NS(responses=Responses(roteiro), conversations=Conversas())


def executor():
    chamados = []

    def ex(nome, args):
        chamados.append((nome, args))
        return {"ok": True}
    ex.chamados = chamados
    return ex


def bot_falso(classe, *roteiro):
    bot = classe.__new__(classe)
    bot.openai, bot.conversation_id = cliente(*roteiro), "conv_1"
    if classe is atendimento.Atendente:
        bot._textos = {}
    return bot


def conversas_usadas(bot):
    return bot.openai.responses.conversas


def resposta_de(bot, msg="oi", cid=None):
    """Texto devolvido por perguntar (o Despachante devolve 2 itens e o Atendente, 3)."""
    return bot.perguntar(msg, cid)[0] if cid else bot.perguntar(msg)[0]


class TestClassificacao(unittest.TestCase):
    CASOS = [("RateLimitError", 429, "limite"), ("HttpError", 429, "limite"),
             ("APITimeoutError", None, "tempo"), ("APIConnectionError", None, "conexao"),
             ("InternalServerError", 500, "servidor"), ("HttpError", 503, "servidor"),
             ("HttpError", 499, "outro"), ("BadRequestError", 400, "outro"), ("ValueError", None, "outro"),
             ("AuthenticationError", 401, "autenticacao"), ("HttpError", 403, "autenticacao"),
             ("PermissionDeniedError", 403, "autenticacao"),
             ("CredentialUnavailableError", None, "autenticacao"),
             ("ClientAuthenticationError", None, "autenticacao")]

    def test_motivo_de_cada_falha(self):
        for nome, status, esperado in self.CASOS:
            with self.subTest(nome=nome, status=status):
                self.assertEqual(agent.classificar_falha(erro(nome, status)), esperado)

    def test_sem_causa_e_limite_de_voltas(self):
        self.assertEqual(agent.classificar_falha(None), "limite_de_voltas")

    def test_mensagem_amigavel_nao_vaza_detalhes(self):
        for nome, status, _ in self.CASOS:
            with self.subTest(nome=nome, status=status):
                falha = agent.AgenteIndisponivel(erro(nome, status))
                self.assertTrue(falha.mensagem.strip())
                self.assertNotIn("10.0.0.5", falha.mensagem)
                self.assertNotIn("flb_live", falha.mensagem)
                self.assertNotIn("10.0.0.5", str(falha))

    def test_toda_falha_tem_mensagem(self):
        for motivo in {"autenticacao", "limite", "tempo", "conexao", "servidor", "outro", "limite_de_voltas"}:
            self.assertTrue(agent.MENSAGENS_FALHA[motivo].strip())

    def test_autenticacao_orienta_a_fazer_az_login(self):
        self.assertIn("az login", agent.AgenteIndisponivel(erro("AuthenticationError", 401)).mensagem)


class TestClienteResiliente(unittest.TestCase):
    def test_aplica_timeout_e_tentativas(self):
        recebido = {}

        class Cliente:
            def with_options(self, **kw):
                recebido.update(kw)
                return "cliente-novo"

        self.assertEqual(agent.cliente_resiliente(Cliente()), "cliente-novo")
        self.assertEqual(recebido, {"timeout": agent.TIMEOUT_S, "max_retries": agent.TENTATIVAS_SDK})

    def test_valores_razoaveis(self):
        self.assertLessEqual(agent.TIMEOUT_S, 90)     # o padrão do SDK é 600 s
        self.assertGreaterEqual(agent.TIMEOUT_S, 20)  # respostas com ferramentas levam alguns segundos
        self.assertTrue(1 <= agent.TENTATIVAS_SDK <= 5)

    def test_sem_with_options_devolve_o_mesmo_objeto(self):
        obj = object()
        self.assertIs(agent.cliente_resiliente(obj), obj)


class TestExecutarTurno(unittest.TestCase):
    def test_caminho_feliz_continua_igual(self):
        ex, c = executor(), cliente(com_chamadas(chamada("consultar_pedido", '{"identificador": "LO1"}')),
                                    texto("Pronto."))
        resposta, chamadas = agent.executar_turno(c, "conv_1", "oi", ex)
        self.assertEqual(resposta, "Pronto.")
        self.assertEqual([x["funcao"] for x in chamadas], ["consultar_pedido"])
        self.assertEqual(ex.chamados, [("consultar_pedido", {"identificador": "LO1"})])
        devolvido = c.responses.pedidos[1][0]
        self.assertEqual((devolvido["type"], devolvido["call_id"]), ("function_call_output", "call_1"))
        self.assertEqual(json.loads(devolvido["output"]), {"ok": True})

    def test_argumentos_invalidos_viram_erro_sem_chamar_a_ferramenta(self):
        ex = executor()
        _, chamadas = agent.executar_turno(cliente(com_chamadas(chamada(args="{nao-json")), texto()),
                                           "conv_1", "oi", ex)
        self.assertIn("erro", chamadas[0]["resultado"])
        self.assertEqual(ex.chamados, [])

    def test_falha_na_primeira_chamada(self):
        causa = erro("RateLimitError", 429)
        with self.assertRaises(agent.AgenteIndisponivel) as cm:
            agent.executar_turno(cliente(causa), "conv_1", "oi", executor())
        self.assertEqual(cm.exception.motivo, "limite")
        self.assertEqual(cm.exception.chamadas, [])
        self.assertIs(cm.exception.__cause__, causa)

    def test_falha_no_meio_preserva_as_ferramentas_ja_executadas(self):
        ex = executor()
        with self.assertRaises(agent.AgenteIndisponivel) as cm:
            agent.executar_turno(cliente(com_chamadas(chamada("consultar_pedido")), erro("APITimeoutError")),
                                 "conv_1", "oi", ex)
        self.assertEqual(cm.exception.motivo, "tempo")
        self.assertEqual([c["funcao"] for c in cm.exception.chamadas], ["consultar_pedido"])
        self.assertEqual(len(ex.chamados), 1)

    def test_limite_de_voltas_levanta_em_vez_de_deixar_a_conversa_pendurada(self):
        c = cliente(com_chamadas(chamada()))          # o modelo pede ferramenta para sempre
        with self.assertRaises(agent.AgenteIndisponivel) as cm:
            agent.executar_turno(c, "conv_1", "oi", executor(), max_iter=3)
        self.assertEqual(cm.exception.motivo, "limite_de_voltas")
        self.assertIn("limite de tentativas", cm.exception.mensagem)
        self.assertEqual(len(c.responses.pedidos), 3)
        self.assertEqual(len(cm.exception.chamadas), 3)

    def test_ctrl_c_nao_e_engolido(self):
        with self.assertRaises(KeyboardInterrupt):
            agent.executar_turno(cliente(KeyboardInterrupt()), "conv_1", "oi", executor())


class TestLigacao(unittest.TestCase):
    """Os construtores precisam passar o cliente do modelo por cliente_resiliente.
    Os testes não criam o cliente do Azure, então a ligação é conferida no código-fonte."""

    def _todas_as_chamadas_estao_embrulhadas(self, classe):
        fonte = inspect.getsource(classe.__init__)
        todas = re.findall(r"get_openai_client\(", fonte)
        embrulhadas = re.findall(r"cliente_resiliente\(\s*project\.get_openai_client\(", fonte)
        self.assertTrue(todas)
        self.assertEqual(len(embrulhadas), len(todas))

    def test_despachante(self):
        self._todas_as_chamadas_estao_embrulhadas(agent.Despachante)

    def test_atendente(self):
        self._todas_as_chamadas_estao_embrulhadas(atendimento.Atendente)


class TestCaminhoFeliz(unittest.TestCase):
    def test_despachante(self):
        bot = bot_falso(agent.Despachante, texto("Olá!"))
        self.assertEqual(bot.perguntar("oi", "conv_A"), ("Olá!", []))
        self.assertEqual(conversas_usadas(bot), ["conv_A"])

    def test_atendente(self):
        bot = bot_falso(atendimento.Atendente, texto("Olá, cliente!"))
        self.assertEqual(bot.perguntar("oi", "conv_A"), ("Olá, cliente!", [], []))
        self.assertEqual(conversas_usadas(bot), ["conv_A"])

    def test_nenhuma_conversa_extra_e_criada(self):
        for classe in CLASSES:
            with self.subTest(classe=classe.__name__):
                bot = bot_falso(classe, texto())
                resposta_de(bot, cid="conv_A")
                self.assertEqual(bot.openai.conversations.criadas, 0)


class TestFalhas(unittest.TestCase):
    """Comportamento de Despachante.perguntar e Atendente.perguntar quando o modelo falha."""

    def test_falha_vira_mensagem_amigavel(self):
        casos = [("RateLimitError", 429, "muitas requisições"), ("APITimeoutError", None, "demorou demais"),
                 ("APIConnectionError", None, "conexão"), ("InternalServerError", 500, "instável"),
                 ("AuthenticationError", 401, "az login")]
        for classe in CLASSES:
            for nome, status, trecho in casos:
                with self.subTest(classe=classe.__name__, erro=nome):
                    bot = bot_falso(classe, erro(nome, status))
                    self.assertIn(trecho, resposta_de(bot, cid="conv_A"))

    def test_a_mensagem_do_cliente_continua_no_historico_apos_falha(self):
        bot = bot_falso(atendimento.Atendente, erro("APITimeoutError"))
        bot.perguntar("meu código é ABC12345", "conv_A")
        self.assertEqual(len(bot._textos["conv_A"]), 1)

    def test_falha_sem_ferramentas_mantem_a_conversa(self):
        # nada ficou pendurado: tentar de novo na MESMA conversa, sem perder o contexto
        for classe in CLASSES:
            with self.subTest(classe=classe.__name__):
                bot = bot_falso(classe, erro("RateLimitError", 429), texto("Olá!"))
                self.assertIn("muitas requisições", resposta_de(bot, cid="conv_A"))
                self.assertEqual(resposta_de(bot, cid="conv_A"), "Olá!")
                self.assertEqual(conversas_usadas(bot), ["conv_A", "conv_A"])
                self.assertEqual(bot.openai.conversations.criadas, 0)

    def test_falha_depois_de_ferramenta_troca_a_conversa_na_proxima_mensagem(self):
        for classe in CLASSES:
            with self.subTest(classe=classe.__name__):
                bot = bot_falso(classe, com_chamadas(chamada()), erro("APITimeoutError"), texto("Olá!"))
                self.assertIn("demorou demais", resposta_de(bot, cid="conv_A"))
                self.assertEqual(bot.openai.conversations.criadas, 0)      # ainda não: só na próxima mensagem
                self.assertEqual(resposta_de(bot, cid="conv_A"), "Olá!")   # a página continua passando conv_A
                self.assertEqual(conversas_usadas(bot), ["conv_A", "conv_A", "conv_nova_1"])
                self.assertEqual(bot.openai.conversations.criadas, 1)

    def test_a_conversa_substituta_e_mantida_nas_mensagens_seguintes(self):
        for classe in CLASSES:
            with self.subTest(classe=classe.__name__):
                bot = bot_falso(classe, com_chamadas(chamada()), erro("APITimeoutError"), texto("Olá!"))
                for _ in range(4):
                    resposta_de(bot, cid="conv_A")
                self.assertEqual(conversas_usadas(bot), ["conv_A", "conv_A"] + ["conv_nova_1"] * 3)
                self.assertEqual(bot.openai.conversations.criadas, 1)

    def test_outra_conversa_nao_e_afetada(self):
        for classe in CLASSES:
            with self.subTest(classe=classe.__name__):
                bot = bot_falso(classe, com_chamadas(chamada()), erro("APITimeoutError"), texto("Olá!"))
                resposta_de(bot, cid="conv_A")
                resposta_de(bot, cid="conv_B")
                self.assertEqual(conversas_usadas(bot)[-1], "conv_B")
                self.assertEqual(bot.openai.conversations.criadas, 0)

    def test_nova_falha_na_substituta_abre_outra(self):
        for classe in CLASSES:
            with self.subTest(classe=classe.__name__):
                bot = bot_falso(classe, com_chamadas(chamada()), erro("APITimeoutError"),
                                com_chamadas(chamada()), erro("APITimeoutError"), texto("Olá!"))
                resposta_de(bot, cid="conv_A")
                resposta_de(bot, cid="conv_A")
                self.assertEqual(resposta_de(bot, cid="conv_A"), "Olá!")
                self.assertEqual(conversas_usadas(bot), ["conv_A", "conv_A", "conv_nova_1", "conv_nova_1",
                                                         "conv_nova_2"])

    def test_limite_de_voltas_tambem_troca_a_conversa(self):
        for classe in CLASSES:
            with self.subTest(classe=classe.__name__):
                bot = bot_falso(classe, com_chamadas(chamada()))      # pede ferramenta para sempre
                self.assertIn("limite de tentativas", resposta_de(bot, cid="conv_A"))
                resposta_de(bot, cid="conv_A")
                usadas = conversas_usadas(bot)
                n = len(usadas) // 2
                self.assertEqual(usadas[:n], ["conv_A"] * n)
                self.assertEqual(usadas[n:], ["conv_nova_1"] * n)

    def test_falha_ao_criar_a_substituta_vira_mensagem_amigavel_e_tenta_de_novo_depois(self):
        for classe in CLASSES:
            with self.subTest(classe=classe.__name__):
                bot = bot_falso(classe, com_chamadas(chamada()), erro("APITimeoutError"), texto("Olá!"))
                resposta_de(bot, cid="conv_A")
                bot.openai.conversations.falhar = True
                self.assertIn("conexão", resposta_de(bot, cid="conv_A"))
                self.assertEqual(conversas_usadas(bot), ["conv_A", "conv_A"])   # nem chegou a chamar o modelo
                bot.openai.conversations.falhar = False
                self.assertEqual(resposta_de(bot, cid="conv_A"), "Olá!")
                self.assertEqual(conversas_usadas(bot)[-1], "conv_nova_1")

    def test_sem_id_informado_usa_a_conversa_padrao_do_objeto(self):
        for classe in CLASSES:
            with self.subTest(classe=classe.__name__):
                bot = bot_falso(classe, com_chamadas(chamada()), erro("APITimeoutError"), texto("Olá!"))
                resposta_de(bot)
                self.assertEqual(resposta_de(bot), "Olá!")
                self.assertEqual(conversas_usadas(bot), ["conv_1", "conv_1", "conv_nova_1"])

    def test_chamadas_ja_feitas_voltam_junto_com_a_mensagem(self):
        bot = bot_falso(agent.Despachante, com_chamadas(chamada()), erro("APIConnectionError"))
        resposta, chamadas = bot.perguntar("oi", "conv_A")
        self.assertIn("conexão", resposta)
        self.assertEqual([c["funcao"] for c in chamadas], ["ferramenta_inexistente"])
        bot = bot_falso(atendimento.Atendente, com_chamadas(chamada()), erro("APIConnectionError"))
        resposta, chamadas, fontes = bot.perguntar("oi", "conv_A")
        self.assertEqual(([c["funcao"] for c in chamadas], fontes), (["ferramenta_inexistente"], []))


if __name__ == "__main__":
    unittest.main(verbosity=1)
