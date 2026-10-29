# B3 — Agente MVP (caminho feliz)

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Depende de | B1, B2 |
| Desbloqueia | B4, B5 |
| Critério da avaliação | "Funciona de ponta a ponta?", rastreabilidade |
| Status | ☐ A fazer |

## Objetivo

Um agente que conversa com o lead do "oi" até a cotação correta, com o fluxo controlado pelo grafo do LangGraph, o preço vindo sempre da API e cada passo registrado.

## Princípio

**O LLM interpreta, o código decide.** O LLM extrai dados e redige frases. Transições, perguntas obrigatórias, chamada da cotação e texto do preço são código.

## Tarefas

- [ ] `graph/state.py`: estado do grafo.
- [ ] `graph/nodes.py`: nós (lista abaixo), cada um uma função pequena e testável.
- [ ] `graph/builder.py`: monta o `StateGraph`, arestas condicionais e `AsyncPostgresSaver` (`setup()` no start). Para as tabelas do checkpointer ficarem no schema `langgraph`, abrir a conexão dele com `options="-c search_path=langgraph"`. Conferir na versão instalada se o saver já aceita schema por parâmetro; se não, o `search_path` resolve. A conexão precisa de `autocommit=True` e `row_factory=dict_row`, como pede a documentação do saver.
- [ ] `conversation/slots.py`: merge de `Extraction` em `LeadSlots`, validação e próxima pergunta.
- [ ] `conversation/templates.py`: templates fixos (confirmação, cotação, carência, pro-rata).
- [ ] `safety/price_guardrail.py`: validador de valores em R$.
- [ ] `llm/composer.py` + `llm/prompts/composer.md`: interface `Composer` com `LLMComposer` (redação das falas que não envolvem preço) e `ScriptedComposer` (testes).
- [ ] `agent.py`: `Agent.handle_message(conversation_id, text, message_type) -> list[OutboundMessage]`.
- [ ] `cli.py`: comandos `autoseguro chat` e `autoseguro timeline`.

## Especificação

### Estado do grafo

```python
class AgentState(TypedDict):
    conversation_id: str
    inbound: InboundMessage            # mensagem atual (já mascarada)
    extraction: Extraction | None
    slots: LeadSlots
    status: ConversationStatus         # COLETANDO, CONFIRMANDO, COTANDO, COTADO, ...
    awaiting: str | None               # qual pergunta está pendente
    turns_without_progress: int
    quotes: list[QuoteSummary]         # quote_id, plano_id, premio_mensal
    outbox: list[OutboundMessage]      # respostas deste turno
```

Cada mensagem do lead é **uma execução do grafo** com `thread_id = conversation_id`. O checkpointer guarda o estado entre os turnos.

### Nós e fluxo

```
ingest → extract → update_slots ──► route
                                     ├─ faltam dados ──────► ask_missing ──► respond
                                     ├─ dados completos ───► confirm ──────► respond
                                     ├─ lead confirmou ────► quote ─► present ─► respond
                                     ├─ lead corrigiu ─────► update_slots (volta ao confirm)
                                     ├─ dúvida ────────────► answer_question ─► respond
                                     └─ (B4) handoff / objeção / degradação
```

| Nó | Faz | LLM? |
|---|---|---|
| `ingest` | Grava a mensagem (`messages`) e o evento `message_received` | Não |
| `extract` | Chama a extração tipada (B2) | Sim |
| `update_slots` | Normaliza e valida; slot inválido vira pergunta de esclarecimento; evento `slots_updated` (só os nomes dos campos, sem valores) | Não |
| `route` | Aresta condicional; decide o próximo nó a partir de `status`, `intent` e `slots.missing()` | Não |
| `ask_missing` | Escolhe a próxima pergunta, na ordem: veículo e ano → idade → CEP → plano → data de início | Redação |
| `confirm` | Template: "Confere: {veículo} {ano}, {idade} anos, CEP {cep}, plano {plano}, início em {data}?" | Não |
| `quote` | Envia "Só um instante, estou calculando…"; chama o `QuoteClient` (B1) | Não |
| `present` | Monta a cotação com o template a partir do `QuoteResult` | Não |
| `answer_question` | Responde dúvidas sobre planos, coberturas, franquia e carência usando **só** `/planos` | Sim |
| `respond` | Passa o texto pelo guardrail de preço, grava `message_sent` | Não |

### Regras da conversa

- **Apresentação:** a primeira mensagem diz que é um assistente virtual da AutoSeguro.
- **Uma pergunta por vez**, mas aceitar vários dados na mesma mensagem ("tenho 35, cep 01310-100").
- **CEP obrigatório.** Sem CEP a API cota mais barato do que o correto.
- **Plano:** se o lead não souber, apresentar os 3 planos (nome, coberturas e franquia de `/planos`, **sem preço**) e perguntar. O `base_mensal` de `/planos` **não é preço para o lead** (falta aplicar idade, veículo e região) e nunca é mostrado. Se ele pedir "quanto fica cada um", cotar os 3 pela API.
- **Data de início:** perguntar; aceitar "hoje", "amanhã", datas. Se não for dia 1º, avisar que o primeiro pagamento é proporcional.
- **Ano-modelo futuro** (`veiculo_ano` > ano atual): perguntar o ano de fabricação antes de cotar. O tratamento completo do caso fica no B4.
- **Confirmação obrigatória** antes de chamar a API.
- **Não pedir** CPF, e-mail, placa nem nome completo.

### Template da cotação

```
Cotação {plano_nome} (nº {quote_id_curto}):
• Mensalidade: R$ {premio_mensal}
• Franquia: R$ {franquia}
• Coberturas: {coberturas}
{se pro-rata} • Primeiro pagamento (proporcional, {dias_cobrados} de {dias_no_mes} dias): R$ {valor_primeiro_pagamento}
• Atenção: roubo e furto têm carência de {dias} dias a partir do início da vigência.
Quer seguir com esse plano ou prefere comparar com outro?
```

- Valores formatados em pt-BR (`R$ 1.234,56`) a partir de `Decimal`.
- O texto vem de template. O LLM **não** reescreve a mensagem de cotação.

### Guardrail de preço

- Regex `R\$\s?\d{1,3}(\.\d{3})*(,\d{2})?` em toda mensagem de saída.
- Valores permitidos = `premio_mensal`, `franquia` e `valor_primeiro_pagamento` de todas as cotações **desta conversa** + as franquias dos 3 planos de `/planos` (são dados fixos do produto, usados ao explicar planos e na objeção de franquia). `base_mensal` **não** entra na lista.
- Se aparecer um valor que não está na lista: a mensagem é descartada, trocada por uma resposta segura em template e o evento `guardrail_blocked` é gravado (com o valor encontrado e o nó de origem).

### Comandos

```bash
uv run autoseguro chat                    # nova conversa; imprime o conversation_id
uv run autoseguro chat --id <uuid>        # retoma uma conversa (checkpointer)
uv run autoseguro chat --debug            # mostra eventos ao lado das mensagens
uv run autoseguro timeline <uuid>         # linha do tempo: mensagens + eventos + cotações
uv run autoseguro timeline <uuid> --export runs/01_caminho_feliz   # .md + .jsonl
```

## Critérios de aceite

- [ ] Com o LLM real e o caos padrão, uma conversa do "oi" até a cotação: o valor exibido é igual ao `premio_mensal` da API e confere com o oráculo.
- [ ] CEP de alto risco (ex.: 21…): a mensalidade reflete o multiplicador 1.30.
- [ ] Início no meio do mês: a mensagem mostra o primeiro pagamento proporcional; início no dia 1º: não mostra.
- [ ] A carência de roubo e furto aparece em toda cotação.
- [ ] Interromper o chat e retomar com `--id` continua de onde parou.
- [ ] `timeline` mostra cada mensagem e cada tentativa de cotação, com ids e status.
- [ ] O guardrail bloqueia um valor inventado (teste com o `ScriptedComposer` respondendo "R$ 99,90") e deixa passar "franquia de R$ 1.500,00".

## Testes

- [ ] Unitários: merge de slots, ordem das perguntas, `route` (tabela estado × intenção → nó), template de cotação (com e sem pro-rata), formatação monetária, guardrail.
- [ ] Integração do grafo com `ScriptedExtractor` + `ScriptedComposer` + `respx`: caminho feliz completo; correção de dado na confirmação; vários dados numa mensagem só; lead que pergunta da franquia antes de informar dados.
- [ ] Persistência: o estado sobrevive a reiniciar o processo (checkpointer real no Postgres de teste).

## Fora do escopo

- Handoff, objeções, cotação pendente e fallback do LLM (B4).
- Webhook e debounce (B5).

## Riscos e cuidados

- Manter os nós pequenos e sem efeito colateral escondido: todo I/O passa por `EventStore`, `QuoteClient` ou `ChatModel`, injetados.
- O `answer_question` pode "inventar" coberturas: o prompt recebe o JSON de `/planos` e o guardrail também verifica R$ nessas respostas.
- Não colocar o histórico inteiro no prompt: mandar o resumo dos slots + as últimas N mensagens (já mascaradas).
