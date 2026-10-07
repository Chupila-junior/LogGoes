"""Ferramentas do agente de ATENDIMENTO AO CLIENTE.

Privilégio mínimo: este agente só tem UMA ferramenta (consultar_rastreio), que devolve o
mínimo necessário ao cliente. Ele não conhece as ferramentas do despachante (listar
motoristas, propor atribuição etc.), então não há como usá-las, nem por engano nem por
manipulação da conversa.
"""
from azure.ai.projects.models import FunctionTool

import fleetbase_client as fb

FERRAMENTAS_ATENDIMENTO = [
    FunctionTool(
        name="consultar_rastreio",
        description="Consulta a situação de um pedido pelo CÓDIGO DE RASTREIO informado pelo cliente. "
                    "Devolve situação, horário agendado, se está atrasado e o bairro de destino. "
                    "Não aceita outro tipo de código.",
        parameters={
            "type": "object",
            "properties": {"codigo": {"type": "string",
                                      "description": "Código de rastreio do pedido, como informado pelo cliente."}},
            "required": ["codigo"],
            "additionalProperties": False,
        },
        strict=True,
    ),
]


def executar_ferramenta_atendimento(nome, args, textos_cliente=None):
    """Executa a ferramenta pedida. Sempre devolve algo serializável, inclusive em caso de erro.

    `textos_cliente`: o que o cliente escreveu na conversa (já normalizado com
    fb.normalizar_codigo). Se for informado, só se consulta um código que aparece nesse texto.
    Assim, o modelo não consegue consultar um código alterado por engano nem um código que o
    cliente nunca escreveu.
    """
    try:
        if nome == "consultar_rastreio":
            codigo = args.get("codigo")
            if not isinstance(codigo, str) or not codigo.strip():
                return {"erro": "Informe o código de rastreio."}
            if textos_cliente is not None:
                alvo = fb.normalizar_codigo(codigo)
                if not any(alvo in texto for texto in textos_cliente):
                    return {"erro": "O código consultado não corresponde ao que o cliente escreveu. "
                                    "Peça ao cliente para enviar o código de rastreio novamente."}
            return fb.consultar_rastreio_publico(codigo[:40])
        return {"erro": f"Ferramenta desconhecida: {nome}"}
    except fb.FleetbaseError:
        return {"erro": "Não consegui consultar o pedido agora. Tente novamente em instantes."}
    except Exception as e:  # nunca deixar o loop do agente travar
        return {"erro": f"Falha inesperada: {type(e).__name__}"}
