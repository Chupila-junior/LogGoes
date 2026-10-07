# LogGoes: Fleetbase + agentes no Microsoft Foundry

Dois agentes de IA sobre um **Fleetbase** local, com públicos e permissões diferentes:

- **Despachante** (uso interno): conversa em português, consulta pedidos e motoristas e propõe a
  atribuição de um motorista a um pedido, que só é executada depois da confirmação do usuário.
- **Atendimento ao cliente** (uso externo): responde dúvidas com base em **documentos**
  (base de conhecimento, busca em arquivos) e consulta a situação de um pedido pelo código de
  rastreio, devolvendo só o mínimo necessário.

Projeto do curso de Microsoft Foundry (SENAI).

## O que ele faz

- **Painel de despacho** com indicadores, **mapa** dos pedidos (um ponto por destino, com a cor do pedido mais urgente ali), lista de
  pedidos com filtros e busca, e lista de motoristas (livres primeiro).
- Consulta pedidos por código interno (`LO...`), código de rastreio ou id.
- Lista pedidos atrasados, motoristas livres e um resumo do dia.
- **Propõe** a atribuição de um motorista a um pedido sem motorista, pelo chat ou pelo atalho
  "Atribuir motorista" na aba Pedidos. O usuário confirma ou cancela em um cartão na tela;
  só então o Fleetbase é alterado. Os dois caminhos usam o mesmo fluxo de proposta e confirmação.
- Recusa o que não sabe fazer (criar pedidos, trocar motorista, cancelar) em vez de inventar.
- **Atendimento ao cliente** (segunda página): responde sobre prazos, atrasos, reagendamento,
  cancelamento, entrega ausente e avarias com base nos documentos de `base_conhecimento/`,
  mostrando as **fontes**, e consulta o pedido pelo código de rastreio.

## Arquitetura

```
 Navegador ── Streamlit (app.py)
                  │  pergunta do usuário
                  ▼
            agent.py ──────────────►  Microsoft Foundry (Azure)
        (loop de function calling)     agente "despachante-logistico"
                  ▲                    modelo gpt-5.4-mini (Global Standard)
                  │  pede: function_call
                  │  devolve: function_call_output
                  ▼
   tools.py  (valida os argumentos do modelo)
                  │
                  ▼
   fleetbase_client.py ──────────────►  Fleetbase (Docker, na sua máquina)
   (dados enxutos, horário de SP)        API pública em http://localhost:8000/v1
```

O agente de **atendimento** segue o mesmo desenho, com duas diferenças: ele tem uma ferramenta de
busca em arquivos (`FileSearchTool`, que roda no Azure sobre os documentos enviados) e uma
**única** função local, `consultar_rastreio` (`tools_atendimento.py`). Ele não conhece as
ferramentas do despachante.

O que roda onde: **o modelo e o agente ficam no Azure; as funções rodam na sua máquina.** O
agente nunca acessa o Fleetbase diretamente: ele só *pede* uma função, e o código local a
executa. Por isso não é preciso expor o Fleetbase na internet.

Fluxo de uma pergunta: o agente devolve itens `function_call`, o código executa as funções e
devolve `function_call_output` na mesma conversa, e isso se repete (no máximo 6 vezes) até
vir a resposta em texto.

## Arquivos

| Arquivo | Função |
|---|---|
| `app.py` | Ponto de entrada: configuração, estilo comum e navegação no topo entre as duas páginas |
| `pagina_despacho.py` | Painel do despachante: indicadores, mapa, abas de pedidos e motoristas, chat e cartão de confirmação |
| `pagina_atendimento.py` | Atendimento ao cliente: chat, fontes citadas, limites do atendimento e códigos de teste |
| `agent.py` | Despachante: cria o agente no Foundry e executa o loop de function calling (também no terminal) |
| `tools.py` | Ferramentas do despachante, validação dos argumentos e propostas aguardando confirmação |
| `atendimento.py` | Agente de atendimento: publica/apaga a base, cria o agente e conversa (também no terminal) |
| `tools_atendimento.py` | A única ferramenta do atendimento (`consultar_rastreio`) e a trava contra códigos alterados |
| `base_conhecimento/` | Documentos (`.md`) da base: prazos, atrasos, reagendamento, entrega ausente, perguntas frequentes |
| `preflight.py` | Verificação pré-apresentação (só leitura): ambiente, Fleetbase, dados da demonstração, login, Foundry, base e backup |
| `kb_smoke_test.py` | Testa a busca em arquivos no Foundry (permissões, indexação, citações) e apaga o que criou |
| `kb_state.json` | Gerado ao publicar a base (id do vector store). Fica fora do Git |
| `fleetbase_client.py` | Chamadas à API do Fleetbase; devolve dados enxutos e aplica as regras de negócio |
| `seed_fleetbase.py` | Popula dados de simulação; pode ser interrompido e retomado |
| `hello_agent.py` | Teste de conexão com o Foundry (cria o agente e troca duas mensagens) |
| `requirements.txt` | Dependências com as versões testadas |
| `.env.example` | Modelo das variáveis de ambiente |
| `.streamlit/config.toml` | Tema escuro (opcional; veja a instalação, passo 3) |

## Regras de negócio (definidas no código, não no Fleetbase)

- **Atrasado:** pedido que não está concluído nem cancelado e cujo horário agendado já passou.
  O Fleetbase não tem esse status.
- **Motorista livre:** não tem nenhum pedido ativo atribuído. O status e o campo `online` do
  motorista no Fleetbase não refletem isso.
- **Horários:** a API devolve UTC; o cliente converte para o horário de São Paulo.
- **Sem previsão de chegada:** `distance` e `time` vêm zerados no Fleetbase local, então o
  agente informa apenas o horário agendado.

## Segurança

- **O modelo nunca grava no Fleetbase.** A ferramenta `propor_atribuicao` só valida e guarda
  um rascunho. A gravação acontece quando o usuário clica em *Confirmar*, usando os ids do
  rascunho (não o que o modelo disser na hora). O rascunho é de uso único e expira em 10 min.
- **Os argumentos do modelo são tratados como entrada não confiável** e validados antes de
  chegar à API.
- **Após gravar, o código relê o pedido** para confirmar que o motorista foi mesmo atribuído,
  em vez de supor sucesso.
- **Limite de 6 iterações** no loop do agente, contra loops infinitos e gasto de crédito.

Atendimento ao cliente (privilégio mínimo):

- **Uma só ferramenta**, sem acesso às do despachante: não há como listar motoristas, propor
  atribuições nem alterar pedidos, por engano ou por manipulação da conversa.
- **Só aceita o código de rastreio.** O código interno (`LO...`) e os ids (`order_...`) não
  funcionam. A comparação ignora caixa, acentos, espaços e hífens (os códigos reais têm letras
  acentuadas, como `Gó8881740690BR`).
- **Devolve o mínimo:** situação, horário agendado, atraso, se há motorista (sim ou não) e o
  bairro do destino. Nunca nome ou telefone de motorista, origem, ids ou observações internas.
- **Trava contra código alterado:** só se consulta um código que aparece no que o cliente
  escreveu na conversa. Se o modelo trocar um caractere, a consulta é recusada antes de chegar ao
  Fleetbase; o "não encontrei" mostra o código consultado e é genérico (não confirma se existe).
- **Escopo por instrução:** o agente só atende assuntos de entregas da LogGoes. Isso depende de o
  modelo obedecer, então não é uma garantia absoluta (ao contrário das restrições acima, que
  são do código).
- Credenciais no `.env` (fora do Git). O acesso ao Azure usa `az login`, sem chave no código.

## Pré-requisitos

- Docker Desktop (no Windows, com WSL2) e Git
- Python 3.12 e Node.js (para a CLI do Fleetbase)
- Azure CLI e uma assinatura Azure com um projeto no Microsoft Foundry
- Cerca de 8 GB de RAM livres para o Fleetbase

## Instalação

### 1. Fleetbase (local)

```bash
npm install -g @fleetbase/cli
flb install-fleetbase        # host: localhost, ambiente: Development
```

Use uma pasta vazia e nova como diretório de instalação (por exemplo `~/fleetbase`). O console
fica em http://localhost:4200 e a API em http://localhost:8000.

Se o console mostrar *"Fleetbase is not installed or configured"*, rode o deploy dentro da
pasta do Fleetbase:

```bash
docker compose exec application bash -c "./deploy.sh"
```

Se o deploy falhar com `Access denied ... to database 'fleetbase_sandbox'`, libere os bancos
`fleetbase_*` para o usuário da aplicação e rode o deploy de novo:

```bash
docker compose exec database sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD"'
```
```sql
GRANT ALL PRIVILEGES ON `fleetbase\_%`.* TO 'fleetbase'@'%';
FLUSH PRIVILEGES;
EXIT;
```

Depois, no console: crie a conta e a organização e gere uma chave em
**Developers > API Keys** (use a chave secreta, de preferência `flb_live_`).

### 2. Microsoft Foundry

1. Crie um projeto em https://ai.azure.com (recurso "Microsoft Foundry").
2. Implante um modelo pequeno com cobrança por uso. Foi usado o `gpt-5.4-mini` com
   *Implantação de Padrão Global* (Global Standard). **Evite tipos Provisioned**, que cobram
   por hora mesmo sem uso.
3. Crie um alerta de orçamento em Cost Management.
4. Atribua a função **Foundry User** (antes *Azure AI User*) à sua conta no recurso, em
   Controle de acesso (IAM). Sem ela, criar o agente falha com erro de permissão.
5. Faça login: `az login --use-device-code`.

### 3. Este projeto

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env         # e preencha os valores
```

Tema escuro do painel (opcional; reinicie o Streamlit depois de criar):

```bash
mkdir -p .streamlit
cat > .streamlit/config.toml <<'EOF'
[theme]
base = "dark"
primaryColor = "#3b82f6"
backgroundColor = "#0f1623"
secondaryBackgroundColor = "#171f2e"
textColor = "#e5e7eb"
EOF
```

Valide cada camada separadamente, nesta ordem:

```bash
python seed_fleetbase.py --check     # Fleetbase e chave de API
python hello_agent.py                # Foundry, modelo e permissões
```

### 4. Dados de simulação

```bash
python seed_fleetbase.py             # cria o que faltar; pode ser repetido com segurança
python seed_fleetbase.py --status    # mostra o que já existe
```

Cria 4 veículos, 6 motoristas, 8 locais em São Paulo e 10 pedidos. O sexto motorista fica
**sem pedidos** (é o motorista livre da demonstração).

Os horários dos pedidos são relativos ao momento em que o seed roda, então com o passar do
tempo mais pedidos ficam atrasados. **Rode o seed pouco antes de apresentar.**
Para refazer os pedidos, apague-os pelo console (Fleet-Ops > Pedidos). A opção
`--reset-orders` tenta apagar pela API, mas é experimental.

### 5. Base de conhecimento do atendimento

```bash
python kb_smoke_test.py                  # opcional: confirma permissões, indexação e citações (apaga o que criou)
python atendimento.py --publicar-base    # envia os documentos de base_conhecimento/ ao Foundry
```

Os documentos são **políticas fictícias** da LogGoes, criadas para o projeto. A busca em arquivos
tem **cobrança adicional** além dos tokens, então a base é pequena (cerca de 6,5 KB) e expira
sozinha 7 dias após o último uso. Republicar apaga a base anterior primeiro. **Ao terminar o
curso, apague a base**: `python atendimento.py --apagar-base`.

Para enviar arquivos, a conta precisa da função **Storage Blob Data Contributor** na conta de
armazenamento do projeto, além de **Foundry User** no projeto. Os arquivos aceitos incluem
`.md`, `.txt`, `.pdf` e `.docx`.

## Executar

```bash
streamlit run app.py          # interface em http://localhost:8501 (menu no topo: Despacho | Atendimento)
python agent.py               # alternativa: chat do despachante no terminal
python atendimento.py         # alternativa: chat do atendimento no terminal
```

Cada vez que um agente inicia, é criada uma nova *versão* dele no Foundry. Isso é
esperado e tem custo desprezível.

## Roteiro de demonstração

Antes: rode a verificação e resolva o que ela apontar.

```bash
python preflight.py            # completa (uma chamada ao agente gasta poucos tokens)
python preflight.py --rapido   # sem chamar o agente (não gasta tokens)
```

Ela só **lê**: confere o Docker e a chave do Fleetbase, os dados da demonstração (um motorista
livre e um pedido atrasado sem motorista), o login do Azure, o agente, a base de conhecimento
(existe, está indexada e não expira antes da apresentação) e o último backup (completo e
recente). Cada falha vem com a ação para resolvê-la, e a chave nunca é impressa. Depois, deixe o
Streamlit aberto.

1. Mostre o **painel**: indicadores no topo (pedidos, atrasados, sem motorista, motoristas
   livres) e o **mapa**, com os atrasados em vermelho. Passe o mouse sobre um ponto.
2. Aba **Pedidos**: use o filtro *Atrasados* e a busca. Aba **Motoristas**: a Beatriz Rocha
   aparece como a única livre.
3. No chat: `Quais pedidos estão atrasados?` Abra *Consultas feitas pelo agente* para mostrar
   que ele consultou o Fleetbase em vez de inventar.
4. `Quais pedidos atrasados estão sem motorista?` e anote o código.
5. `Atribua um motorista livre ao pedido <código>`. Aparece o **cartão de confirmação** no topo;
   o Fleetbase ainda não mudou. Clique em *Confirmar*: o indicador *Sem motorista* diminui, o
   pedido sai do vermelho no mapa e o console do Fleetbase mostra o pedido atualizado.
6. Mostre o **atalho sem chat**: aba Pedidos > *Atribuir motorista a um pedido* > escolha o
   pedido e o motorista > *Propor*. Cai no mesmo cartão de confirmação.
7. Repita o pedido para um código que já tem motorista: o agente informa quem é.
8. `Onde está o pedido LO999999?` Responde que não encontrou, sem inventar.
9. `Crie uma entrega da Paulista para Moema.` Recusa, explicando o que ainda não faz.

### Atendimento ao cliente (segunda página)

Abra a página **Atendimento ao cliente** no menu do topo. Em *Códigos de teste* (painel da
direita) há códigos reais, com o atrasado primeiro; clicar em um deles envia a pergunta.

1. `O que acontece se meu pedido atrasar?` Responde pela política, com a **fonte** citada
   (`02_politica_de_atrasos.md`) sob a resposta e no painel.
2. Clique no código do pedido **atrasado**: o agente consulta o rastreio (aparece *Consulta ao
   pedido*), reconhece o atraso, pede desculpas e cita a política do tempo de atraso. Mostra a
   base de conhecimento e a função trabalhando juntas.
3. `Quem é o motorista do meu pedido?` Recusa: não informa dados de motoristas.
4. `Cancela o meu pedido.` Explica que não consegue e indica a central.
5. `Qual a capital da França?` Recusa e volta ao assunto de entregas.
6. Compare com o despachante: ele **propõe** a atribuição e o usuário confirma; o atendimento
   só **informa**. São dois agentes com ferramentas e permissões diferentes.

## Estado do projeto e limitações

- Só há **uma operação de escrita**: atribuir motorista a um pedido que ainda não tem um.
  Não cria pedidos, não troca motorista e não cancela.
- Os documentos da base de conhecimento são fictícios. O atendimento só informa: não reagenda,
  não cancela e não concede cupons ou reembolsos (encaminha para a "central", que é fictícia).
- Cada agente aceita **um** vector store; a base fica no Azure e gera cobrança adicional.
- As coordenadas dos locais do seed foram digitadas à mão e são **aproximadas**: ao dar muito zoom,
  o ponto pode ficar a algumas centenas de metros do endereço real. Para precisão de rua, é
  preciso geocodificar os endereços.
- O mapa mostra pedidos (coleta, destino e rota), **não os motoristas**: a simulação não tem
  posição de GPS deles. O mapa usa tiles de internet, então precisa de conexão.
- O agente depende de o computador ter o Docker e o Streamlit rodando; o playground do portal
  do Foundry **não executa** as funções locais.
- O alvo de teste é um Fleetbase local em modo de desenvolvimento, não um ambiente de produção.
- A criação do agente exige permissão no projeto do Foundry (veja a instalação, passo 2).

## Backup do Fleetbase

O banco do Fleetbase guarda a conta, a organização, a chave de API e os pedidos. Na pasta do
Fleetbase:

```bash
docker compose exec database sh -c 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --all-databases' > backup_fleetbase.sql
tail -n 3 backup_fleetbase.sql    # a última linha deve ser "-- Dump completed ..."
```

Restaurar (**procedimento não testado**; sobrescreve o banco atual):

```bash
docker compose exec -T database sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD"' < backup_fleetbase.sql
docker compose restart
```

O arquivo contém dados da conta e chaves: **não o versione nem o compartilhe**.

## Solução de problemas

| Sintoma | Causa provável e o que fazer |
|---|---|
| `no configuration file provided: not found` | O `docker compose` foi rodado fora da pasta do Fleetbase. Entre nela primeiro. |
| `fleetbase_not_configured` (HTTP 503) ou tela *not installed or configured* | O banco está sem as tabelas. Rode o `deploy.sh` (veja a instalação, passo 1). |
| `Access denied ... fleetbase_sandbox` | Falta o `GRANT` nos bancos `fleetbase_*` (veja a instalação, passo 1). |
| `az: command not found` | Instale o Azure CLI: `curl -sL https://aka.ms/InstallAzureCLIDeb \| sudo bash`. |
| `does not have permissions for ... agents/write` | Falta a função **Foundry User** no recurso do Foundry. |
| `A base de conhecimento ainda não foi publicada` | Rode `python atendimento.py --publicar-base`. |
| Erro 403 ao publicar a base ou ao rodar `kb_smoke_test.py` | Faltam funções: **Foundry User** no projeto e **Storage Blob Data Contributor** na conta de armazenamento do projeto. |
| O atendimento diz `O código consultado não corresponde ao que o cliente escreveu` | A trava impediu uma consulta com um código que o cliente não escreveu (o modelo o alterou). Reenvie o código. |
| O atendimento não acha um código de rastreio que existe | Copie o código pelo painel *Códigos de teste* (os códigos reais têm letras acentuadas, como `Gó...`). |
| `ModuleNotFoundError: pydeck` | Instale com `pip install pydeck` (normalmente vem junto com o Streamlit). |
| A página tem rolagem ou o chat fica cortado | A tela é menor que a prevista. Ative o botão **Compacto** no cabeçalho, use `Ctrl` + `-` no navegador, ou ajuste `ALTURA_NORMAL` e `ALTURA_COMPACTA` no topo do `app.py`. Enquanto há uma proposta pendente, o mapa e o chat encolhem `DESCONTO_PROPOSTA` px para a faixa de confirmação caber. |
| Mapa em branco | Sem internet para carregar o mapa de fundo, ou o navegador bloqueou. Recarregue; o resto do painel funciona sem o mapa. |
| `ModuleNotFoundError: azure` | O ambiente virtual não está ativo: `source .venv/bin/activate`. |
| `Defina FOUNDRY_PROJECT_ENDPOINT ...` | Falta preencher o `.env` ou o comando foi rodado fora da pasta do projeto. |
| `UnicodeEncodeError ... surrogates` | O terminal enviou um byte inválido. O código já descarta esses bytes; confira `echo $LANG` (deve terminar em `UTF-8`). |
| Seed dá erro de telefone já cadastrado | Use a versão atual do seed, que reaproveita o que já existe. |
| `--check` falha logo após subir o Docker | O Fleetbase leva 1 a 2 minutos para ficar pronto. Espere e tente de novo. |
