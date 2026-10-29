# B1 — Cotação resiliente ⭐

| Campo | Valor |
|---|---|
| Prioridade | P0 (o bloco que mais pesa) |
| Depende de | B0 |
| Desbloqueia | B3, B4 |
| Critério da avaliação | "O que ele faz quando a `/quote` falha?" (o que mais separa), rastreabilidade |
| Status | ☑ Pronto |

## Objetivo

Um cliente da API de cotação que **nunca trava, nunca inventa valor e registra cada tentativa**. Ele precisa recuperar sozinho as falhas transitórias, desistir rápido quando a API está fora do ar e devolver um resultado tipado que o agente sabe tratar.

## Contexto

Comportamento da API (`quote-service/app/main.py`):
- 20% das chamadas: erro 500/502/503 imediato.
- 10% das chamadas: dormem 8 s e depois **respondem com sucesso**.
- 422: recusa de negócio (idade, veículo, plano inexistente). É determinística.
- 400: payload inválido. É bug nosso.
- `/health` responde sempre, mesmo quando a `/quote` falha: **não serve como indicador**.

## Tarefas

- [x] `quote/client.py`: `QuoteClient` assíncrono sobre `httpx.AsyncClient` (um cliente por processo, reaproveitando conexões).
- [x] `quote/errors.py`: classificação de erro (tabela abaixo).
- [x] `quote/breaker.py`: circuit breaker próprio, em memória, com testes.
- [x] `quote/cache.py`: cache em memória com TTL, chave determinística.
- [x] `quote/outcomes.py`: os 4 desfechos tipados.
- [x] `store/base.py` (interfaces `EventStore` e `QuoteRepository`), `store/postgres.py` e `store/memory.py` (testes e `--no-db`). *Mudou em relação ao plano (`store/events.py` + `store/quotes.py`): agrupar por implementação deixou as interfaces num lugar só.*
- [x] `cli.py`: comando `autoseguro quote` para cotar direto (sem LLM).
- [x] `tests/oracle.py`: implementação de referência das regras de `plans.json`, **usada só em testes e na avaliação** (nunca para responder ao lead).
- [x] Testes de integração com `respx`, contra o Postgres real, do oráculo contra a API real e de caos contra o container.

## Especificação

### Interface

```python
class QuoteClient:
    async def quote(self, req: QuoteRequest, *, conversation_id: UUID) -> QuoteOutcome: ...

QuoteOutcome = QuoteSucceeded | QuoteRefused | QuoteInvalid | QuoteUnavailable

@dataclass
class QuoteSucceeded:   quote_id: UUID; result: QuoteResult; attempts: int; from_cache: bool
@dataclass
class QuoteRefused:     quote_id: UUID; motivo: str            # 422: regra de aceitação
@dataclass
class QuoteInvalid:     quote_id: UUID; detalhe: str           # 400: bug nosso, alerta
@dataclass
class QuoteUnavailable: quote_id: UUID; reason: Literal["retries_exhausted", "deadline", "circuit_open"]; attempts: int
```

O cliente **nunca lança exceção** para o chamador por falha da API: sempre devolve um `QuoteOutcome`. Exceção só para bug de programação.

### Classificação de erro

| Situação | Tipo | Retry? |
|---|---|---|
| Timeout (`httpx.TimeoutException`) | `timeout` | Sim |
| Erro de conexão (`httpx.ConnectError`, `RemoteProtocolError`) | `connection` | Sim |
| 500, 502, 503, 504 | `server_error` | Sim |
| 429 | `rate_limited` | Sim (respeitar `Retry-After`, se vier) |
| 422 com `error=cotacao_recusada` | `refused` | **Não** → `QuoteRefused` |
| 422 com `error=cotacao_recusada` e motivo "Plano '…' inexistente" | `invalid_payload` | **Não** → `QuoteInvalid`. Como `plano_id` é um enum do nosso lado, isso indica bug, não recusa |
| 422 com corpo `{"detail": [...]}` (validação do FastAPI: campo faltando, idade > 200, ano fora de 1950–2100) | `invalid_payload` | **Não** → `QuoteInvalid`. **O 422 tem dois significados nessa API**: distinguir pelo corpo, nunca só pelo status |
| 400 (`error=payload_invalido`, ex.: `data_inicio` fora do formato ISO) | `invalid_payload` | **Não** → `QuoteInvalid` + log de erro |
| 200 com corpo que não valida em `QuoteResult` | `bad_response` | Sim, dentro do mesmo orçamento das outras falhas transitórias (sem regra especial de "uma vez") |
| Outros 4xx | `client_error` | Não → `QuoteInvalid` |

### Política de retry

| Parâmetro | Valor padrão | Variável |
|---|---|---|
| Timeout por tentativa | `min(2.5 s, tempo restante até o prazo)` (connect 1 s) | `QUOTE_TIMEOUT_S` |
| Máximo de tentativas | 4 | `QUOTE_MAX_ATTEMPTS` |
| Prazo total | 12 s | `QUOTE_DEADLINE_S` |
| Backoff | exponencial com *full jitter*: `random(0, min(2.0, 0.3 × 2^n))` | — |

**Implementado como um laço explícito, sem `tenacity`** (a dependência foi removida). Motivos: o timeout de cada tentativa depende do tempo que sobra do prazo, e o relógio, o `sleep` e o `random` precisam ser injetados nos testes. Com `tenacity` isso exigiria contornos; o laço tem ~40 linhas e se lê de cima para baixo.

**Retry acontece só aqui.** O LLM, o grafo e o worker não repetem a mesma chamada por conta própria.

Conta para o README: com 70% de sucesso por tentativa, P(4 falhas) = 0.3⁴ ≈ 0.8%.

### Circuit breaker

- **Fechado:** chamadas passam. Conta falhas *consecutivas* das que permitem retry.
- **Abre** após 5 falhas consecutivas. Enquanto aberto, `quote()` devolve `QuoteUnavailable(reason="circuit_open")` **sem chamar a API**.
- **Meio-aberto** após 30 s: deixa passar uma chamada de teste. Se der certo, fecha; se falhar, abre de novo.
- Uma recusa (422) ou um 400 **não** contam como falha (a API respondeu).
- Cada mudança de estado emite o evento `breaker_state_changed`.
- Se uma falha abre o breaker no meio de uma cotação, ela termina na hora com `circuit_open`, sem esperar o backoff.
- Nota para o README: com 20% de falha aleatória, o breaker quase nunca abre (são precisas 5 falhas seguidas). Ele existe para a queda total.

### Cache

- Chave: `sha256` do JSON canônico de `QuoteRequest` + **data de hoje**. A API calcula a idade do veículo com a data do dia, então a mesma entrada pode mudar de resultado na virada do ano.
- TTL de 15 min. Guarda **apenas** `QuoteSucceeded` e `QuoteRefused` (que são determinísticos). Falhas nunca vão para o cache.
- Um acerto no cache também gera `quote_id` e eventos, com `from_cache=true`.

### Eventos emitidos

| Evento | Payload |
|---|---|
| `quote_requested` | `quote_id`, `request` (sem PII; o CEP aparece só como prefixo) |
| `quote_attempt` | `quote_id`, `attempt_no`, `status_code`, `error_kind`, `latency_ms` |
| `quote_succeeded` | `quote_id`, `plano_id`, `premio_mensal`, `attempts`, `from_cache` |
| `quote_refused` | `quote_id`, `motivo` |
| `quote_invalid` | `quote_id`, `detalhe` |
| `quote_unavailable` | `quote_id`, `reason`, `attempts`, `elapsed_ms` |
| `breaker_state_changed` | `from`, `to` |

### Comando de linha

```bash
uv run autoseguro quote --plano completo --idade 35 --ano 2022 --cep 01310-100 --inicio 2026-11-15
```

Mostra cada tentativa (número, status, latência) e o resultado final. Opções: `--repeat N` (várias cotações no mesmo processo, com resumo de tempos e desfechos), `--no-cache` (toda repetição chama a API), `--no-db` (grava em memória) e `--json`.

### Oráculo de teste (`tests/oracle.py`)

Reimplementa `plans.json` (faixa etária, idade do veículo, região, pro-rata) com **a mesma aritmética da API** (multiplicação em `float` e `round(x, 2)`), recebendo a data de referência por parâmetro, para conferir se o que a API devolve, e o que o agente mostra, está certo. É usado nos testes e no B6. **Nunca** é importado pelo código de produção. Um teste de arquitetura garante isso (procura `oracle` nos imports de `src/`).

## Critérios de aceite

- [x] Com o caos padrão (20%/10%), 50 cotações seguidas pelo comando: todas terminam em `QuoteSucceeded` ou `QuoteUnavailable` em até ~12 s, e nenhuma trava. *(`autoseguro quote ... --repeat 50 --no-cache`: 49 sucessos e 1 `retries_exhausted`; p50 0,08 s, p95 3,27 s, máximo 6,91 s; 1,46 tentativa por cotação em média.)*
- [x] Com `docker-compose.chaos-down.yml` (100% de falha): após 5 falhas o breaker abre, e as chamadas seguintes respondem em < 50 ms com `circuit_open`. *(1ª cotação: 4 tentativas, `retries_exhausted`; 2ª: a 5ª falha abre o breaker; 3ª: 0 tentativas, 0 ms. `CHAOS_MODE=down pytest -m chaos` passou.)*
- [x] Com `docker-compose.chaos-slow.yml` (0% de falha, 100% de lentidão): cada tentativa é cortada em ≤ 2.5 s, nenhuma cotação passa de ~12 s, e o resultado é `QuoteUnavailable`. *(4 tentativas de ~2,5 s, total de 11,45 s, `retries_exhausted`; a 2ª cotação abriu o breaker. `CHAOS_MODE=slow pytest -m chaos` passou.)*
- [x] Payload com `data_inicio` inválida → `QuoteInvalid` com uma tentativa. *(Pelo `respx` com o 400 da API: o `QuoteRequest` tipado não deixa montar uma data inválida.)*
- [x] Idade 76 → `QuoteRefused` com uma única tentativa. *(Contra a API real: 1 tentativa, 422 em 78 ms.)*
- [x] Toda chamada deixa linhas em `quotes`, `quote_attempts` e `events` com o mesmo `quote_id`. *(`tests/integration/test_quote_store_postgres.py` + conferência no banco depois das rodadas manuais.)*
- [x] O preço devolvido bate com o oráculo em 100% dos casos de teste. *(17 casos em `tests/api/test_oracle_vs_api.py` contra a API real, com o caos padrão ligado.)*

### Achados da validação

- **CLI no console do Windows:** imprimir "→" num console cp1252 derrubava o processo. Corrigido (saída com `errors="replace"` e "->" no lugar da seta).
- **Cotação sem desfecho:** esse crash deixou uma linha em `agent.quotes` com `status = 'pending'`. Se o processo morre no meio de uma cotação, ela fica assim. Está registrado como limitação; o worker do B4 pode tratar cotações `pending` antigas como abandonadas.

## Testes

**Integração (`respx`, sem rede):**
- [x] 503 → 503 → 200: sucesso com `attempts=3`.
- [x] timeout → 200: sucesso com `attempts=2`.
- [x] 4 × 500: `QuoteUnavailable(retries_exhausted)`.
- [x] 422 `cotacao_recusada`: `QuoteRefused`, 1 tentativa.
- [x] 422 `{"detail": [...]}` (validação do FastAPI): `QuoteInvalid`, 1 tentativa.
- [x] 422 "Plano inexistente": `QuoteInvalid`.
- [x] 400: `QuoteInvalid`, 1 tentativa.
- [x] 200 com JSON inválido → 200 válido: sucesso.
- [x] Prazo total estoura antes do limite de tentativas: `QuoteUnavailable(deadline)`.
- [x] Breaker: 5 falhas → aberto → chamada não sai (`respx` confirma 0 chamadas) → após 30 s (relógio injetado) → meio-aberto → sucesso fecha.
- [x] Cache: segunda chamada igual não chama a API; uma falha não entra no cache.

**Unitários:** classificação de erro, cálculo de backoff (com `random` injetado), breaker, chave de cache.

**Caos (marcados `@pytest.mark.chaos`, rodam contra o container):** cenários de 100% de falha e 100% de lentidão.

**Oráculo vs API:** tabela de casos (cada faixa etária, cada faixa de idade do veículo, os 5 prefixos de CEP de risco, pro-rata, dia 1º, as 4 recusas) comparando a API real com o oráculo. *Sem CEP ficou de fora: o agente sempre manda CEP.*

## Fora do escopo

- O que dizer ao lead quando falha e a cotação pendente: B4.
- Breaker compartilhado entre processos (anotar como próximo passo).

## Riscos e cuidados

- `time.sleep` no servidor ocupa uma thread do servidor mesmo depois que o cliente desiste. Não é problema nosso, mas vale a nota no README.
- Relógio e aleatoriedade sempre injetados, para os testes não ficarem instáveis.
- **Virada do ano.** A API calcula a idade do veículo com a data do servidor. Testes contra a API real devem usar anos **relativos** (`hoje.year - 3`, `hoje.year - 21`), nunca fixos. Com ano fixo, um teste de "carro 2005 recusado" passa hoje e o de "carro 2006 aceito" quebra em 1º/jan/2027, provavelmente durante a avaliação.
- Não ligar o retry de transporte do `httpx` (`retries` no transport): seria retry em duas camadas.
