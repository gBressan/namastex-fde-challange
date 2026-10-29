# Cards de entrega — Agente AutoSeguro

Cada card é um bloco de entrega independente, com objetivo, tarefas, especificação, critérios de aceite e testes. A visão geral da arquitetura e o porquê das decisões estão em [`../PROPOSTA_ARQUITETURA.md`](../PROPOSTA_ARQUITETURA.md).

## Antes de começar

- [ ] **Avisar a Namastex que começou.** O README do desafio pede isso, para marcarem a conversa de feedback.
- [ ] Criar o repositório público e trazer o desafio no commit `52a006c` (o último em 29/10/2026; ver B0).
- [ ] Criar o token do Hugging Face com a permissão "Make calls to Inference Providers" e conferir se os modelos candidatos (B2) estão disponíveis no router.
- [ ] Guardar esta sessão de planejamento em `ai-logs/` desde já.

## Quadro

| Card | Prioridade | Depende de | Critério da avaliação | Status |
|---|---|---|---|---|
| [B0 — Fundação](B0-fundacao.md) | P0 | — | Qualidade | ☐ A fazer |
| [B1 — Cotação resiliente](B1-cotacao-resiliente.md) | P0 ⭐ | B0 | Falha da `/quote`, rastreabilidade | ☐ A fazer |
| [B2 — Privacidade e escolha do modelo](B2-privacidade-e-modelo.md) | P0 | B0 | Dados sensíveis | ☐ A fazer |
| [B3 — Agente MVP](B3-agente-mvp.md) | P0 | B1, B2 | Ponta a ponta, rastreabilidade | ☐ A fazer |
| [B4 — Handoff e degradação](B4-handoff-e-degradacao.md) | P0 | B3 | Critério de handoff, falha da `/quote` | ☐ A fazer |
| [B5 — Canal WhatsApp](B5-canal-whatsapp.md) | P2 (opcional) | B3 | Prontidão para produção | ☐ A fazer |
| [B6 — Evidência e análise](B6-evidencia-e-analise.md) | P1 | B2, B4 | Qualidade, uso do dataset | ☐ A fazer |
| [B7 — Entrega](B7-entrega.md) | P0 | todos | Qualidade, uso de IA | ☐ A fazer |

Legenda de status: ☐ A fazer · ◐ Em andamento · ☑ Pronto

## Dependências

```
B0 Fundação ──┬──► B1 Cotação resiliente ──┐
              │                            ├──► B3 Agente MVP ──► B4 Handoff e degradação ──► B7 Entrega
              └──► B2 Privacidade e modelo ┘         │                    │
                                                     └──► B5 Canal        └──► B6 Evidência ──► B7
```

- B1 e B2 podem andar em paralelo.
- **Entrega mínima defensável:** B0 + B1 + B2 (Silver mínima) + B3 + B4 + B7.
- **Se faltar tempo, cortar nesta ordem:** B5 → simulação em lote do B6 → camada Gold do B6 → retry diferido do B4 (vira handoff imediato com mensagem honesta).

## Definição de pronto (vale para todos os cards)

Um card só vira ☑ quando:

- [ ] Os critérios de aceite do card foram verificados à mão, e o comando usado está anotado no card.
- [ ] `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` e `uv run pytest` passam localmente e na CI.
- [ ] Toda ação relevante emite evento no event store, com `conversation_id`.
- [ ] Nenhum dado pessoal (CPF, telefone, e-mail, placa) aparece em log, evento ou prompt.
- [ ] Decisões novas foram registradas em `docs/adr/` (quando houver) e o README foi atualizado, se mudou algo de "como rodar".
- [ ] Os commits são pequenos e com mensagem clara.
- [ ] As sessões de IA usadas no card foram guardadas em `ai-logs/`.

## Convenções

- Código em inglês (nomes, docstrings); mensagens ao lead, docs e ADRs em português.
- Pacote Python: `autoseguro` em `agent/src/autoseguro/`.
- Tudo é configurado por variáveis de ambiente (`pydantic-settings`), com exemplo em `.env.example`.
- IDs: UUID gerado pela aplicação (`uuid4`), em formato texto nos logs.
