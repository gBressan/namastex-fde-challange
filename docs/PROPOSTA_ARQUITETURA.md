# Proposta de arquitetura — Agente AutoSeguro (desafio FDE Namastex)

> Documento de trabalho. Serve de base para a seção "Decisões" do README final.

## 1. O que o desafio realmente avalia

O README lista os critérios. Lidos com atenção, o peso fica assim:

| Critério | O que demonstrar |
|---|---|
| Ponta a ponta | Conversa → qualificação → cotação correta → decisão. Caminho feliz sem quebrar. |
| **Falha da `/quote`** ("o que mais separa") | Timeout, retry com backoff, circuit breaker, **nunca inventar preço**, UX honesta pro lead. |
| Handoff explícito | Regras determinísticas, com *reason code*, testáveis. |
| Rastreabilidade | Cada mensagem/cotação com id e status. Dá pra reconstruir uma conversa. |
| Dados sensíveis | Minimização (LGPD), mascaramento em logs/dataset/prompt. |
| Qualidade | Código legível, decisões documentadas (ADRs curtos). |
| Uso de IA | `ai-logs/` mostrando orquestração crítica, não "copia e cola". |

## 2. Armadilhas encontradas lendo o código (use isso no README)

Lendo `quote-service/` e `scripts/generate_dataset.py`:

1. **CEP opcional barateia o preço.** Sem `cep`, o multiplicador de região é 1.0. Se o agente não coletar o CEP, cota **mais barato que o correto** para prefixos de alto risco (07, 08, 21, 26, 59 → ×1.30). → CEP é obrigatório no agente.
2. **CEP mal formatado gera preço errado.** A API usa `cep.replace("-","").strip()[:2]`. "CEP 21310-100" vira prefixo "CE" e "(21) 310-100" vira "(2", e nos dois casos o agravo de região some. → Normalizar para 8 dígitos antes de enviar.
3. **`plano_id` tem default `essencial`.** Omitir o plano cota silenciosamente o Essencial. → Sempre enviar explícito.
4. **Carro "ano-modelo" futuro é recusado.** A idade do veículo é `hoje.year - veiculo_ano`. Hoje (out/2026) um 0 km ano-modelo 2027 dá −1 ano e cai em "fora das faixas aceitas". → Tratar: perguntar ano de fabricação ou mandar para humano.
5. **Recusas são de negócio (422) e não devem ter retry.** Idade > 75, < 18, veículo > 20 anos (no dataset, ~11% dos leads têm > 75 e ~20% têm carro ≤ 2005).
6. **400 = erro nosso** (payload). Também sem retry; é bug.
7. **`/health` é "sempre estável"**: não indica se a `/quote` está de pé. Não usar como readiness da cotação.
8. **Lentidão de 8 s retorna sucesso.** Sem timeout, o agente trava 8 s. Com timeout curto + retry, recupera mais rápido.
9. **`data_inicio` no meio do mês** gera `primeiro_pagamento_pro_rata`: o agente precisa comunicar o primeiro pagamento proporcional. A API não valida data passada, então o agente valida.
10. **Carência de 30 dias para roubo/furto** vem na resposta: precisa ser comunicada (é a "regra que só se aplica em casos específicos").
11. **O dataset ensina preço errado.** Os vendedores cotam valores aleatórios ("Premium por R$ 219,90… cobre colisão, roubo e furto"), incoerentes com a tabela. **Não usar falas de vendedor como few-shot de preço/cobertura**, ou o LLM aprende a alucinar.
12. **Timestamps fora de ordem.** O gerador soma minutos aleatórios por índice. Ordenar por `message_index`, nunca por `timestamp`.
13. **Leads mandam rajadas** (várias mensagens seguidas, mídia no meio). → Agregar mensagens com uma janela de debounce antes de responder.
14. **Os vendedores pedem CPF, mas a cotação não usa CPF.** Princípio da necessidade (LGPD art. 6º, III): o agente **não pede CPF**. Se o lead mandar espontaneamente, mascara e descarta.
15. O gerador comenta "mascará-los na camada **Silver**". Eles esperam uma pipeline Bronze → Silver → Gold no dataset.

## 3. Princípio central: "o LLM interpreta, o código decide"

O LLM faz o que é bom em fazer: entender texto livre e redigir respostas naturais. Tudo que tem regra (estado da conversa, quando cotar, quando passar para humano, preço) fica em **código determinístico e testável**.

```
          ┌──────────────────────────── Agent Service (FastAPI) ───────────────────────────┐
WhatsApp  │                                                                                │
(simulado)│  Inbound ─► Debounce ─► PII Redactor ─► NLU (LLM, saída tipada Pydantic)        │
 /webhook │                                          │ intent, slots extraídos, confiança  │
 ou CLI   │                                          ▼                                     │
          │                         Conversation State Machine (código puro)               │
          │                          │            │                 │                      │
          │                          ▼            ▼                 ▼                      │
          │                    Slot filling   Quote Orchestrator   Handoff Policy          │
          │                                   (retry, breaker,     (regras + reason code)  │
          │                                    deadline, cache)                            │
          │                                       │                                        │
          │                                       ▼                                        │
          │                    Response Composer (LLM redige; preço vem de template)       │
          │                                       │                                        │
          │                    Price Guardrail: todo "R$" da resposta ∈ resultado da API    │
          │                                       │                                        │
          │  Event Store (Postgres, append-only)◄─┴──► logs JSON estruturados (PII mascarada)│
          └───────────────────────────────────────┬────────────────────────────────────────┘
                                                  │ httpx (timeout 2.5 s)
                                                  ▼
                                        quote-service (legado instável)
```

### Estados da conversa

`SAUDACAO → COLETANDO (veículo, ano, idade, CEP, plano, data_inicio) → CONFIRMANDO_DADOS → COTANDO → COTADO → {FECHAMENTO→HANDOFF | OBJECAO | ENCERRADO}`, com `COTACAO_PENDENTE` e `HANDOFF` como estados laterais.

O LLM nunca escolhe a transição. Ele devolve um objeto tipado, por exemplo `Extraction(intent="informar_dados", idade=35, veiculo_ano=2022, cep="01310100", confidence=0.92)`, e a máquina de estados decide o próximo passo.

**Confirmação antes de cotar:** "Confere: Onix 2022, 35 anos, CEP 01310-100, plano Completo, início 15/11?". É barato e elimina erro de extração.

## 4. Resiliência da cotação (o ponto que mais separa)

Comportamento da API: 20% de erro 5xx imediato, 10% de lentidão de 8 s (que termina em sucesso), 70% ok.

| Mecanismo | Configuração proposta | Por quê |
|---|---|---|
| Timeout por tentativa | 2.5 s | A resposta normal é instantânea; a lenta leva 8 s. Cortar cedo e tentar de novo é mais rápido. |
| Retry | Só em timeout, 5xx e erro de conexão. **Nunca** em 400/422. | 422 é recusa de negócio e 400 é bug. |
| Backoff | Exponencial com *full jitter* (base 0.3 s, teto 2 s) | Evita retries sincronizados. |
| Orçamento | Máximo de 4 tentativas **e** deadline total de ~12 s; o timeout de cada tentativa é `min(2.5 s, tempo restante)` | Limita por tempo, não só por contagem. Sem o `min`, uma tentativa que começa aos 11 s terminaria aos 13,5 s. |
| Circuit breaker | Abre após 5 falhas seguidas, meia-abertura após 30 s | Em queda total (`QUOTE_FAILURE_RATE=1`), para de martelar e vai direto para o fluxo degradado. |
| Cache | Chave = hash do payload normalizado + data do dia (a API calcula a idade do veículo com a data atual), TTL de 15 min | A cotação é função pura do payload; repetir a pergunta não gera nova chamada. |
| Retry em uma camada só | Só o `QuoteClient` faz retry; o LLM/tool não | Evita multiplicar chamadas (retry dentro de retry). |

**Conta para o README:** P(sucesso por tentativa) ≈ 0.7. Com 4 tentativas, P(falha total) = 0.3⁴ ≈ **0.8%**. O pior caso fica em ~12 s.

**Experiência do lead durante a falha:**
1. Ao começar a cotar, o agente manda "Só um instante, estou calculando sua cotação…" (WhatsApp tolera alguns segundos).
2. Se o orçamento estoura ou o breaker está aberto: **não inventa preço**. Responde "Nosso sistema de cotação está instável agora. Já registrei seus dados e te mando o valor em alguns minutos." O estado vira `COTACAO_PENDENTE`.
3. Um worker em background (tabela `pending_quotes` no Postgres, consumida com `SELECT … FOR UPDATE SKIP LOCKED`, que permite vários workers sem pegar o mesmo item) tenta de novo com backoff mais longo (30 s, 60 s, 120 s…). Se der certo, envia a cotação proativamente.
4. Se continuar falhando após N minutos: **handoff** com o payload pronto (`reason=QUOTE_UNAVAILABLE`), para o humano cotar manualmente.

**Decisão consciente a documentar:** a resposta da API traz os `multiplicadores`, então daria para calcular localmente o preço dos outros planos. **Não fazemos isso.** Preço exibido ao lead vem sempre da API (fonte da verdade). O cálculo local existe só como **oráculo nos testes**.

### Guardrail anti-alucinação de preço

- O valor nunca é redigido pelo LLM: o texto da cotação sai de um **template** preenchido com a resposta da API (prêmio, franquia, coberturas, carência, pro-rata).
- Validador final: um regex extrai todo `R$ x` da mensagem de saída. Se algum valor não estiver no `QuoteResult` atual, a mensagem é bloqueada e trocada pelo template. Isso fica registrado como evento `guardrail_blocked`.

## 5. Critério de handoff (explícito e defensável)

Implementado como uma lista ordenada de regras em código (a primeira que casar vence). Cada regra tem um *reason code* e um teste. A especificação completa (como cada uma é detectada) está no card [B4](entregas/B4-handoff-e-degradacao.md).

| # | Gatilho | Reason code | Ação |
|---|---|---|---|
| 1 | Lead pede humano ("quero falar com alguém") | `USER_REQUESTED` | Handoff imediato |
| 2 | Hostilidade ou tentativa de manipular o bot | `SAFETY` | Handoff, sem discutir |
| 3 | Lead aceita a proposta ("fechado", "pode emitir") | `READY_TO_CLOSE` | Handoff para emissão (o agente não emite apólice nem boleto) |
| 4 | Negociação de preço, desconto ou "a concorrente fez mais barato" | `PRICE_NEGOTIATION` | Handoff (o agente não tem alçada de preço) |
| 5 | Cotação indisponível após o retry diferido | `QUOTE_UNAVAILABLE` | Handoff com payload pronto |
| 6 | Recusa de aceitação (idade, veículo) | `UNDERWRITING_REFUSAL` | Informa com cuidado e oferece humano |
| 7 | Caso de borda (ano-modelo futuro, payload inválido, dado contraditório) | `EDGE_CASE` | Handoff |
| 8 | Mídia (áudio, documento, imagem) | `MEDIA_RECEIVED` | Pede texto uma vez; se repetir ou for documento → handoff |
| 9 | Fora de escopo (sinistro, cancelamento, reclamação, outro produto) | `OUT_OF_SCOPE` | Handoff |
| 10 | 3 turnos sem avançar slot, ou confiança de extração < limiar | `LOW_CONFIDENCE` | Handoff |

**Resolve sozinho:** coleta de dados, cotação, explicação de franquia, coberturas, carência e pro-rata, comparação entre planos (cada um cotado via API), lead que desiste.

**Pacote de handoff** (o humano não pergunta nada de novo): resumo da conversa, slots coletados, últimas cotações com id, *reason code*, tentativas de cotação e sentimento. Os dados pessoais vão por referência, não no texto livre.

## 6. Rastreabilidade

- IDs: `conversation_id`, `message_id` (entrada e saída), `turn_id`, `quote_id`, `quote_attempt_id`, `handoff_id`. Todos os eventos e logs carregam `trace_id = conversation_id`.
- **Event store append-only** (Postgres, tabela `events` com `payload JSONB` e índice por `conversation_id`): `message_received`, `slots_updated`, `state_changed`, `quote_requested`, `quote_attempt` (status HTTP, latência, erro), `quote_succeeded`/`quote_failed`, `guardrail_blocked`, `handoff_created`, `message_sent`.
- Logs JSON com `structlog`, com PII mascarada **antes** de logar.
- CLI `autoseguro timeline <conversation_id>` reconstrói a conversa com todos os eventos. Isso vira o "log de execução completa" da entrega.
- (Opcional) OpenTelemetry/Logfire para spans de chamadas do LLM e da API.

## 7. Dados sensíveis (LGPD)

1. **Minimização:** a cotação precisa de idade, ano do veículo, CEP, plano e data. O agente **não pede** CPF, e-mail, placa ou nome completo.
2. **Redação na entrada:** um `PIIRedactor` (regex + validação de dígito verificador do CPF; telefone, e-mail, placa Mercosul e antiga) roda antes de qualquer log, antes do event store e **antes do prompt do LLM**. O CPF vira `[CPF_1]`. O CEP é exceção: é dado de negócio, guarda-se completo no slot, mas nos logs só o prefixo de 2 dígitos (`26***-***`).
3. **Dataset:** a camada Silver mascara a PII. Só a Silver/Gold é usada para few-shot e avaliação.
4. Segredos em `.env` (já está no `.gitignore`) com `pydantic-settings`. Revisar `ai-logs/` antes de commitar.
5. Presidio não tem reconhecedor de CPF pronto; regex + checksum resolve bem e é mais auditável para este escopo. Citar Presidio como evolução.

## 8. Uso do dataset (Bronze → Silver → Gold)

- **Bronze:** o parquet original, intocado.
- **Silver** (DuckDB ou Polars): ordenado por `message_index`, PII mascarada, mídia marcada, `veiculo_texto` normalizado (marca, modelo, ano), prefixo de CEP de risco, e cotação **correta** recalculada pelo oráculo.
- **Gold:** taxa de conversão por plano, faixa etária e região; catálogo de objeções (preço, franquia, concorrente, "preciso pensar") com a frequência de cada uma; % de leads recusáveis.

Usos:
- **Avaliação de extração:** `lead_idade_informada` e `veiculo_texto` são o gabarito. Rodar o NLU sobre as mensagens dos leads e medir acurácia. Isso é um ótimo número para o README.
- **Simulação:** um lead simulado (LLM com a persona tirada da Silver) conversa com o agente. Rodar ~50 a 100 conversas com o caos ligado e medir.
- **Few-shot apenas do lado do lead** (variedade de jeitos de escrever). Nunca as respostas dos vendedores.
- Uma nota de análise curta: "os vendedores cotaram fora da tabela em X% das conversas". É um insight de FDE que mostra por que o agente usa a API.

## 9. Stack recomendada

| Camada | Escolha | Justificativa |
|---|---|---|
| Linguagem/infra | Python 3.12 + `uv` + Docker Compose | O mesmo do `quote-service`; o avaliador sobe tudo com um comando. |
| API do agente | FastAPI (`POST /webhook/messages` no formato de um webhook do WhatsApp) + CLI com Typer/Rich | O CLI serve para demo e geração de log; o webhook mostra que está pronto para Evolution API/Omni. |
| LLM | Modelo aberto do Hugging Face, acessado por uma API compatível com OpenAI (`LLM_BASE_URL`, `LLM_MODEL`). Padrão: **Qwen3.5-27B** via HF Inference Providers. Local: **`qwen3.5:9b`** no Ollama. Ver seção 9.1. | A família Qwen lidera tool calling entre modelos abertos, tem licença Apache 2.0 e vai bem em PT-BR. Trocar de modelo é só mudar configuração. |
| Orquestração | **LangGraph** (stack do autor). A máquina de estados é o `StateGraph`; o handoff é `interrupt()`; o estado da conversa fica no checkpointer Postgres (`langgraph-checkpoint-postgres`); as arestas condicionais chamam a handoff policy em Python puro. | O fluxo fica explícito e pode ser exportado como diagrama Mermaid para o README. O domínio (cliente de cotação, regras, guardrail) não importa nada do LangGraph. |
| HTTP resiliente | `httpx` async + `tenacity` + circuit breaker próprio (~40 linhas) | É explícito e fácil de explicar na entrevista. |
| Persistência | **Postgres 17** no `docker-compose`, com `psycopg` 3 (pool async). O checkpointer do LangGraph e as tabelas do domínio (`events`, `quotes`, `quote_attempts`, `handoffs`, `pending_quotes`) ficam no mesmo banco, em schemas separados. Migrações com Alembic, ou SQL versionado em `db/migrations`. | O avaliador já precisa de Docker para a quote-api, então o custo extra é zero. Ganhos: `JSONB` para eventos, `SKIP LOCKED` para a fila do retry diferido, o checkpointer oficial do LangGraph e o mesmo banco que seria usado em produção. |
| Logs | `structlog` (JSON) | Correlação por `conversation_id`. |
| Dados | DuckDB ou Polars | Lê parquet direto, SQL legível. |
| Qualidade | `pytest`, `pytest-asyncio`, `respx` (mock do httpx), `ruff`, `mypy`, pre-commit, GitHub Actions | CI verde no repo público conta pontos. |

### 9.1 Modelo de LLM

**Papel do modelo:** como o código decide o fluxo, o LLM só faz duas coisas: (1) extrair intenção e dados em formato tipado, com temperatura 0; (2) redigir a resposta, mas nunca o preço, que vem de template. Um modelo médio (9B a 27B) dá conta.

| Uso | Modelo | Como servir |
|---|---|---|
| Padrão (o avaliador roda sem GPU) | `Qwen/Qwen3.5-27B` | HF Inference Providers: `https://router.huggingface.co/v1` + `HF_TOKEN`, usando `ChatOpenAI(base_url=...)` |
| 100% local / offline | `qwen3.5:9b` | Ollama como serviço no `docker-compose` (perfil `local-llm`) |
| Testes e CI | `ScriptedExtractor` / `ScriptedComposer` (implementações das interfaces `Extractor` e `Composer` que leem respostas do YAML do cenário) | Sem rede e sem token; os cenários golden ficam determinísticos. O `GenericFakeChatModel` do LangChain não suporta tool calling, então não serve para testar a saída estruturada. |

- **Escolha baseada em dados:** no bloco B2, com a Silver mínima, rodar a avaliação de extração (cerca de 200 mensagens de lead da Silver, com `lead_idade_informada` e `veiculo_texto` como gabarito) em 2 ou 3 candidatos: Qwen3.5-27B, Qwen3.8-27B (mais novo, ago/2026) e gpt-oss-20b. Escolher pelo trio acurácia × latência × taxa de JSON inválido e colocar a tabela no README.
- **Desligar o "thinking"** na extração (`enable_thinking: false`), por latência. No WhatsApp, cada segundo conta.
- **Saída estruturada:** preferir `with_structured_output(method="function_calling")`, porque o suporte a `json_schema` varia por provedor. Validar com Pydantic e repetir uma vez se o parse falhar.
- **O LLM também é uma dependência instável.** Aplicar timeout e retry e, se ele falhar, cair para extração por regex (idade, ano, CEP) e respostas em template. O agente fica pior, mas não para. É o mesmo princípio usado na `/quote`.
- **LGPD:** usar um provedor de inferência de terceiros é transferência de dados. Isso é mitigado porque a PII é removida antes do prompt e o modelo só recebe dados mínimos. O modo local elimina a transferência. Documentar no README.

## 10. Estrutura do repositório

```
.
├── README.md                    # como rodar + decisões + resultados da avaliação
├── docker-compose.yml           # quote-api + agent
├── quote-service/               # original, intocado
├── agent/
│   ├── pyproject.toml
│   └── src/autoseguro/
│       ├── domain/              # modelos Pydantic, slots, estados, reason codes (sem I/O)
│       ├── conversation/        # handoff policy, slot filling, transições (Python puro)
│       ├── graph/               # StateGraph LangGraph: nós, arestas, checkpointer, interrupt
│       ├── llm/                 # fábrica de modelo (HF/Ollama), Extractor/Composer (LLM e Scripted), prompts
│       ├── quote/               # QuoteClient (retry/breaker/cache), retry worker
│       ├── safety/              # PIIRedactor, price guardrail
│       ├── store/               # event store, repositórios (Postgres) + fakes em memória p/ testes
│       ├── api/                 # FastAPI webhook
│       └── cli.py               # chat, timeline, replay, simulate
│   └── tests/
│       ├── unit/                # redactor, normalizers, policy, breaker
│       ├── integration/         # QuoteClient vs respx (5xx, timeout, 422)
│       ├── scenarios/           # conversas "golden" em YAML com o resultado esperado
│       └── chaos/               # API fora do ar (FAILURE_RATE=1) e lentidão total (FAILURE_RATE=0, SLOW_RATE=1)
├── data_pipeline/               # bronze → silver → gold, notebook/relatório
├── docs/
│   ├── adr/                     # 0001-llm-interpreta-codigo-decide.md, 0002-resiliencia..., ...
│   └── PROPOSTA_ARQUITETURA.md
├── runs/                        # logs de execução completa (happy path + falha + handoff)
└── ai-logs/                     # sessões exportadas de IA (sem segredos)
```

## 11. Testes e avaliação

- **Unitários:** normalização de CEP, idade e ano; redator de PII (CPF válido e inválido); cada regra de handoff; transições de estado; breaker.
- **Integração:** `QuoteClient` com `respx` simulando 503 → 503 → 200, timeout → 200, 422 (sem retry), 400 (sem retry), queda total (breaker abre).
- **Cenários golden** (YAML, com a extração fixada pelo `ScriptedExtractor`): caminho feliz, CEP de alto risco, pro-rata, idade 76, carro 2004, ano-modelo 2027, pedido de humano, "fechado!", negociação, áudio, mensagem com CPF.
- **Simulação com LLM real** (rodada manual, resultados no README): N conversas com o caos padrão (20%/10%). Métricas: % de cotações corretas (comparadas ao oráculo), **preços alucinados = 0**, taxa de handoff por motivo, latência p50/p95 da cotação, tentativas médias.

## 12. Plano em blocos de entrega

> Os cards detalhados de cada bloco (tarefas, especificação, critérios de aceite e testes) estão em [`entregas/`](entregas/README.md).

Cada bloco fecha um incremento que funciona sozinho, com critério de pronto verificável. A ordem segue dependência e risco: o que mais pesa na avaliação vem primeiro. Os blocos B1 e B2 podem andar em paralelo.

```
B0 Fundação ──┬──► B1 Cotação resiliente ──┐
              │                            ├──► B3 Agente MVP ──► B4 Handoff e degradação ──► B7 Entrega
              └──► B2 Privacidade e modelo ┘                         │
                                                  B5 Canal (opcional) ┤
                                                  B6 Evidência ───────┘
```

| Bloco | Critério da avaliação | Essencial? |
|---|---|---|
| B0 Fundação | Qualidade | Sim |
| B1 Cotação resiliente | Falha da `/quote`, rastreabilidade | Sim |
| B2 Privacidade e modelo | Dados sensíveis | Sim (Silver mínima) |
| B3 Agente MVP | Ponta a ponta | Sim |
| B4 Handoff e degradação | Critério de handoff, falha da `/quote` | Sim |
| B5 Canal WhatsApp | Prontidão para produção | Opcional |
| B6 Evidência e análise | Qualidade, uso do dataset | Parcial |
| B7 Entrega | Qualidade, uso de IA | Sim |

### B0 — Fundação
- **Escopo:** repo com uv, ruff, mypy, pytest, pre-commit; `docker-compose` (quote-api + postgres com healthcheck + agent); migrações iniciais; testes de repositório contra um Postgres real (serviço da CI ou testcontainers); CI no GitHub Actions; modelos do domínio (`LeadSlots`, `QuoteRequest`, `QuoteResult`, `HandoffReason`, estados); normalizadores de CEP, idade, ano e data, com testes.
- **Pronto quando:** `docker compose up` sobe a API; `uv run pytest` passa; a CI está verde.

### B1 — Cotação resiliente *(o bloco mais importante)*
- **Escopo:** `QuoteClient` (timeout, classificação de erro, backoff com jitter, deadline, breaker, cache); event store no Postgres registrando cada tentativa; comando `autoseguro quote` para cotar direto.
- **Testes:** `respx` (503→503→200, timeout→200, 422 e 400 sem retry, queda total abrindo o breaker) + caos contra o container (`FAILURE_RATE=1`, `SLOW_RATE=1`).
- **Pronto quando:** com o caos padrão, o comando cota e o log mostra cada tentativa com status e latência; com queda total, falha rápido e sem inventar valor.

### B2 — Privacidade e escolha do modelo
- **Escopo:** `PIIRedactor` (CPF com checksum, telefone, e-mail, placa); Silver mínima (mensagens de lead ordenadas, mascaradas, com gabarito); fábrica de LLM (HF / Ollama) e `ScriptedExtractor` para testes; comparação de 2 ou 3 modelos na extração.
- **Pronto quando:** os testes do redator passam; existe uma tabela acurácia × latência × JSON inválido; o modelo está escolhido e justificado.

### B3 — Agente MVP (caminho feliz)
- **Escopo:** nó de extração (saída estruturada + fallback regex); `StateGraph` (redact → extract → update_slots → ask_missing → confirm → quote → present) com checkpointer; template de cotação (prêmio, franquia, coberturas, carência, pro-rata) + guardrail de preço; chat na linha de comando; comando `timeline`.
- **Pronto quando:** uma conversa real, do "oi" até a cotação correta, roda no chat, e o `timeline` reconstrói a conversa com todos os ids.

### B4 — Handoff e degradação
- **Escopo:** as 10 regras de handoff com reason code; `interrupt()` + pacote de handoff; nó de objeção; `COTACAO_PENDENTE` com retry diferido; fallback quando o LLM falha; cenários golden com o `ScriptedExtractor`.
- **Pronto quando:** cada regra tem um cenário golden passando, incluindo idade 76, carro 2004, ano-modelo 2027, "fechado!", negociação, áudio, CPF espontâneo e `/quote` fora do ar.

### B5 — Canal WhatsApp *(opcional)*
- **Escopo:** webhook FastAPI no formato de mensagem do WhatsApp; agrupamento de rajadas (debounce); idempotência por `message_id`.
- **Pronto quando:** um `curl` no webhook produz a mesma conversa que o chat.

### B6 — Evidência e análise
- **Escopo:** Silver/Gold completas; insights (vendedores cotando fora da tabela, objeções, % recusáveis); simulação em lote com lead simulado e caos ligado.
- **Pronto quando:** existem métricas reais para o README: % de cotações corretas, preços inventados = 0, handoff por motivo, latência p50/p95 da cotação.
- **Mínimo, se faltar tempo:** só os insights da Silver, sem a simulação em lote.

### B7 — Entrega
- **Escopo:** `runs/` com 3 logs (caminho feliz, falha recuperada, handoff); README (como rodar, arquitetura, diagrama Mermaid do grafo, decisões, armadilhas, métricas, limitações, próximos passos); ADRs; `ai-logs/` exportados e limpos.
- **Pronto quando:** o **teste de clone limpo** funciona: clonar o repo, `docker compose up` e seguir o README à risca, sem nenhum passo extra.

**Entrega mínima defensável:** B0 + B1 + B2 (Silver mínima) + B3 + B4 + B7.
**Se faltar tempo, cortar nesta ordem:** B5 → simulação em lote do B6 → Gold → retry diferido do B4 (vira handoff imediato com mensagem honesta).

## 13. Dicas para a entrevista e para os ai-logs

- Esta própria sessão do Claude Code fica em `~/.claude/projects/C--code-namastex-fde-challange/*.jsonl`. Copie para `ai-logs/` no fim. Revise antes por e-mail e chaves.
- Nos logs, mostre que você **validou** o que a IA propôs (por exemplo, encontrar as armadilhas lendo o código, rodar o caos). É isso que eles querem ver.
- Na seção "Limitações", seja honesto: o handoff é simulado, a fila é uma tabela no Postgres (não um broker), o PII é por regex, etc.
