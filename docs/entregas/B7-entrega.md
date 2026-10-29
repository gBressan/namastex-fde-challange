# B7 — Entrega

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Depende de | Todos os outros cards que entraram no escopo |
| Desbloqueia | — |
| Critério da avaliação | Qualidade ("outro engenheiro consegue entender as decisões?"), uso de IA, log de execução completa |
| Status | ☐ A fazer |

## Objetivo

Empacotar tudo de forma que o avaliador consiga **rodar, entender e conferir** sem falar com você, e cumprir os 5 itens obrigatórios do desafio.

## Itens obrigatórios do desafio

| # | Item pedido | Onde fica |
|---|---|---|
| 1 | Agente de ponta a ponta | `agent/` |
| 2 | Repositório público no GitHub | — |
| 3 | README com como rodar e decisões | `README.md` + `docs/adr/` |
| 4 | Log de uma execução completa | `runs/` |
| 5 | Conversas com as IAs | `ai-logs/` |

## Tarefas

### Logs de execução (`runs/`)
- [ ] `runs/01_caminho_feliz/` — conversa completa até a cotação, com caos padrão.
- [ ] `runs/02_falha_recuperada/` — a cotação passa por falhas e retries (visíveis nas tentativas) ou fica pendente e chega depois.
- [ ] `runs/03_handoff/` — ex.: "fechado!" ou negociação com concorrente, mostrando o ticket.
- [ ] (opcional) `runs/04_api_fora_do_ar/` — prazo estoura e o handoff `QUOTE_UNAVAILABLE` acontece.
- [ ] Cada pasta: `transcricao.md` (conversa legível), `eventos.jsonl` (export do `timeline`), `README.md` de 3 linhas dizendo o que observar e o comando usado para gerar.

### README.md (raiz)
- [ ] **Resumo** em 5 linhas: o que é, o que o agente faz, o diferencial.
- [ ] **Como rodar** (copiar e colar): pré-requisitos (Docker obrigatório por causa do Postgres; o desafio original permitia rodar a API sem Docker, então justificar), `.env`, `docker compose up`, `autoseguro chat`, testes, caos.
- [ ] **Arquitetura**: diagrama dos componentes + diagrama Mermaid do grafo, gerado por `graph.get_graph().draw_mermaid()`.
- [ ] **Decisões** (uma linha cada, com link para o ADR).
- [ ] **Armadilhas encontradas** no código do desafio (CEP opcional, ano-modelo futuro, dataset com preços errados, timestamps fora de ordem etc.).
- [ ] **Resiliência da cotação**: tabela de parâmetros, a conta de 0,8%, o que o lead vê em cada cenário.
- [ ] **Critério de handoff**: a tabela das 10 regras.
- [ ] **Dados sensíveis**: minimização, redator, o que nunca é gravado, transferência para o provedor de LLM.
- [ ] **Resultados**: comparação de modelos (B2), métricas da simulação (B6), insights do dataset.
- [ ] **Limitações** (honestas) e **Próximos passos** (Omni/Evolution API, worker separado, breaker distribuído, Presidio, observabilidade com OpenTelemetry, LLM-juiz).
- [ ] **Uso de IA**: como você usou, o que aceitou, o que rejeitou e por quê, com link para `ai-logs/`.

### ADRs (`docs/adr/`, formato curto: contexto → decisão → consequências)
- [ ] 0001 — LLM interpreta, código decide
- [ ] 0002 — Resiliência da cotação (timeout, retry, breaker, cache, retry diferido)
- [ ] 0003 — Preço sempre da API, por template, com guardrail (e por que não calcular localmente)
- [ ] 0004 — Critério de handoff
- [ ] 0005 — Minimização e mascaramento de dados pessoais
- [ ] 0006 — LangGraph como orquestrador
- [ ] 0007 — Modelo aberto via HF Inference Providers (com a comparação)
- [ ] 0008 — Postgres para estado, eventos e fila

### ai-logs/
- [ ] Copiar as sessões do Claude Code de `~/.claude/projects/C--code-namastex-fde-challange/*.jsonl` (e de outras ferramentas usadas).
- [ ] Rodar uma varredura de segredos antes de commitar: procurar `hf_`, `sk-`, `ghp_`, `Bearer`, `password`, e-mails e telefones pessoais. Remover ou trocar por `[REMOVIDO]`.
- [ ] `ai-logs/README.md`: índice com ferramenta, data, assunto de cada sessão e 2 ou 3 momentos em que você corrigiu ou rejeitou a IA.

### Verificação final
- [ ] **Teste de clone limpo**: em outra pasta, `git clone`, copiar `.env.example` para `.env`, colocar só o token, `docker compose up`, seguir o README à risca. Anotar e corrigir qualquer passo que faltou.
- [ ] CI verde no último commit.
- [ ] Varredura de PII no repo inteiro (o mesmo teste do B2, rodando sobre `runs/` e `docs/`).
- [ ] Conferir a tabela abaixo.

## Checklist final: critério → evidência

| Critério da avaliação | Evidência no repo |
|---|---|
| Funciona de ponta a ponta | `runs/01_caminho_feliz`, cenários golden, CI |
| O que faz quando a `/quote` falha | `runs/02`, `runs/04`, testes de caos, ADR 0002, seção do README |
| Critério de handoff explícito | `handoff_policy.py`, tabela no README, `runs/03`, ADR 0004 |
| Rastreabilidade | `timeline`, tabelas `events`/`quotes`/`quote_attempts`, `eventos.jsonl` nos runs |
| Dados sensíveis | Redator + testes, varredura da Silver, ADR 0005 |
| Qualidade | Estrutura, tipos, testes, ADRs, README |
| Uso de IA | `ai-logs/` + seção do README |

## Critérios de aceite

- [ ] O teste de clone limpo passou sem nenhum passo fora do README.
- [ ] Os 5 itens obrigatórios estão no repo.
- [ ] Nenhum segredo ou dado pessoal real no repo (varredura limpa).
- [ ] Cada linha do checklist final tem a evidência apontada e funcionando.

## Riscos e cuidados

- Avisar a Namastex quando começar (o README do desafio pede isso).
- Os logs de IA são públicos: revisar com calma, não só por regex.
- Não deixar o README crescer demais: o que é detalhe vai para o ADR, e o README aponta.
