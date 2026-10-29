# B0 — Fundação

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Depende de | — |
| Desbloqueia | B1, B2 |
| Critério da avaliação | Qualidade ("outro engenheiro consegue pegar seu código e entender") |
| Status | ☐ A fazer |

## Objetivo

Ter um repositório que sobe com um comando, roda testes e CI, e já contém os modelos do domínio e os normalizadores que todos os outros blocos usam.

## Tarefas

### Repositório
- [x] Trazer o desafio **com o histórico** para `C:\code\namastex-fde-challange` (`git init` + remote `upstream` + `pull` do commit `52a006c`). Não é um fork do GitHub: o histórico original fica, e qualquer um confere o que mudou com `git diff 52a006c`.
- [ ] Criar um repo público **novo** (vazio, sem README) no GitHub e adicioná-lo como remote `origin`.
- [ ] `.gitattributes` com `* text=auto eol=lf`: o Git deste Windows está com `core.autocrlf=true`, e scripts `.sh` com CRLF quebram dentro dos containers Linux.
- [ ] **`quote-service/`, `dataset/` e `scripts/` ficam intocados.** Conferível com `git diff 52a006c -- quote-service dataset scripts` (deve sair vazio). O `docker-compose.yml` da raiz é estendido, mas o serviço `quote-api` continua idêntico ao original. Guardar o README original como `docs/DESAFIO.md`.
- [ ] Criar `agent/` como projeto `uv` (`agent/pyproject.toml`, Python 3.12, layout `src/autoseguro/`).
- [ ] Dependências de runtime: `langgraph`, `langgraph-checkpoint-postgres`, `langchain-openai`, `psycopg[binary,pool]`, `httpx`, `tenacity`, `pydantic`, `pydantic-settings`, `structlog`, `typer`, `rich`, `fastapi`, `uvicorn`, `alembic`, `sqlalchemy` (só para o Alembic), `duckdb`.
- [ ] Dependências de dev: `pytest`, `pytest-asyncio`, `respx`, `testcontainers[postgres]`, `ruff`, `mypy`, `pre-commit`.
- [ ] `ruff` (lint + format), `mypy --strict` em `src/`, `pre-commit` com ruff e mypy.
- [ ] `.env.example` com todas as variáveis (ver Especificação); `.env` continua no `.gitignore`.
- [ ] **Adicionar `!.env.example` ao `.gitignore`.** O `.gitignore` original tem `.env.*`, que ignora o `.env.example` (confirmado com `git check-ignore`). Sem a exceção, o avaliador clona o repo sem o arquivo de exemplo.
- [ ] Lembrar que o `.gitignore` original também ignora `*.log`: logs de execução em `runs/` precisam ser `.md` e `.jsonl`.

### Infra local
- [ ] `docker-compose.yml` na raiz com os serviços:
  - `quote-api` (build de `./quote-service`, mesmas variáveis de caos do original);
  - `postgres` (imagem `postgres:17`, volume nomeado, `healthcheck` com `pg_isready`);
  - `agent` (build de `./agent`, `depends_on: postgres: condition: service_healthy`);
  - perfil `local-llm` com `ollama` (opcional, usado no B2).
- [ ] Dois overrides de caos, usados no B1 e no B4:
  - `docker-compose.chaos-down.yml`: `QUOTE_FAILURE_RATE=1.0` (API fora do ar);
  - `docker-compose.chaos-slow.yml`: `QUOTE_FAILURE_RATE=0.0` **e** `QUOTE_SLOW_RATE=1.0`. A API sorteia um único número e testa a falha primeiro; só mudar o `SLOW_RATE` daria 20% de falha e 80% de lentidão.
  - As taxas são lidas quando a API sobe, então trocar de modo exige recriar o container: `docker compose -f docker-compose.yml -f docker-compose.chaos-down.yml up -d quote-api`.
- [ ] `Makefile` ou `justfile` com atalhos: `up`, `down`, `test`, `lint`, `migrate`, `chat`.

### Banco
- [ ] Alembic configurado em `agent/migrations/`, com a migração inicial criando os schemas `agent` (tabelas abaixo) e `langgraph` (vazio). As tabelas do checkpointer são criadas pelo `AsyncPostgresSaver.setup()` no B3.
- [ ] Atenção à URL: o `psycopg` usa `postgresql://…`, mas o SQLAlchemy/Alembic precisa de `postgresql+psycopg://…`. Derivar uma da outra no `Settings`, a partir de um único `DATABASE_URL`.
- [ ] Comando `autoseguro db migrate` (ou `alembic upgrade head`) executado no start do container do agente.

### Domínio (sem I/O)
- [ ] `domain/models.py`: `LeadSlots`, `QuoteRequest`, `QuoteResult`, `PlanId`, `ConversationStatus`.
- [ ] `domain/handoff.py`: enum `HandoffReason` (os 10 códigos definidos no card B4).
- [ ] `domain/normalizers.py`: CEP, idade, ano do veículo, data de início.
- [ ] `config.py`: `Settings` (`pydantic-settings`).
- [ ] `logging.py`: `structlog` em JSON, com `conversation_id` no contexto.

### CI
- [ ] GitHub Actions: `uv sync` → `ruff check` → `ruff format --check` → `mypy` → `pytest`, com serviço `postgres:17` no job.

## Especificação

### Variáveis de ambiente

| Variável | Exemplo | Uso |
|---|---|---|
| `QUOTE_API_URL` | `http://quote-api:8000` | Base da API de cotação |
| `DATABASE_URL` | `postgresql://autoseguro:autoseguro@postgres:5432/autoseguro` | Postgres |
| `LLM_BASE_URL` | `https://router.huggingface.co/v1` | Endpoint compatível com OpenAI |
| `LLM_MODEL` | `Qwen/Qwen3.5-27B` | Modelo |
| `LLM_API_KEY` | `hf_...` | Token (nunca commitar) |
| `LLM_PROVIDER` | `openai_compatible` \| `scripted` | `scripted` nos testes (ver B2) |
| `QUOTE_TIMEOUT_S`, `QUOTE_MAX_ATTEMPTS`, `QUOTE_DEADLINE_S` | `2.5`, `4`, `12` | Usados no B1 |
| `LOG_LEVEL` | `INFO` | |

### Modelos do domínio

```python
class PlanId(StrEnum):
    ESSENCIAL = "essencial"; COMPLETO = "completo"; PREMIUM = "premium"

class LeadSlots(BaseModel):          # tudo opcional: preenchido ao longo da conversa
    veiculo_descricao: str | None    # "Onix 2022" (texto livre, sem PII)
    veiculo_ano: int | None
    idade: int | None
    cep: str | None                  # 8 dígitos, só números
    plano_id: PlanId | None
    data_inicio: date | None
    def missing(self) -> list[str]: ...   # ordem: veiculo_ano, idade, cep, plano_id, data_inicio

class QuoteRequest(BaseModel):       # sempre completo e normalizado
    plano_id: PlanId; idade: int; veiculo_ano: int; cep: str; data_inicio: date
    def to_api_payload(self) -> dict: ...   # cep "01310100" → "01310-100", data ISO

class QuoteResult(BaseModel):        # espelha a resposta 200 da API
    plano_id: PlanId; plano_nome: str; premio_mensal: Decimal; franquia: Decimal
    coberturas: list[str]; multiplicadores: dict[str, float]
    carencia: Carencia; primeiro_pagamento_pro_rata: ProRata | None; moeda: str
```

- Valores monetários sempre em `Decimal`, nunca em `float`.
- `QuoteRequest` **não** replica as regras de aceitação (idade 18–75 etc.): a API é a fonte da verdade. Valida só o formato.

### Normalizadores (funções puras)

| Função | Entrada → saída | Regras |
|---|---|---|
| `normalize_cep` | `"CEP 21310-100"`, `"21.310-100"`, `"(21) 310-100"` → `"21310100"` | Remove tudo que não é dígito; exige 8 dígitos; senão `None` |
| `format_cep` | `"21310100"` → `"21310-100"` | Para o payload da API |
| `normalize_idade` | `"tenho 35 anos"` → `35` | Inteiro entre 0 e 120; fora disso `None` |
| `normalize_ano_veiculo` | `"2022"`, `"ano 22"` → `2022` | 4 dígitos entre 1950 e ano atual + 1; 2 dígitos → 2000+ se ≤ ano atual + 1, senão 1900+ |
| `parse_data_inicio` | `"amanhã"`, `"15/11"`, `"dia 1º"`, `"2026-11-15"` → `date` | Recebe `today` como parâmetro (testável); `dd/mm` sem ano → próxima ocorrência; rejeita data passada e data além de 90 dias |

### Tabelas (schema `agent`)

| Tabela | Colunas principais | Observação |
|---|---|---|
| `conversations` | `id uuid pk`, `channel`, `external_ref_hash`, `status`, `created_at`, `updated_at` | `external_ref_hash` = hash do telefone (nunca o número) |
| `messages` | `id uuid pk`, `conversation_id fk`, `direction (in/out)`, `external_message_id`, `type`, `body_redacted`, `created_at` | `unique (conversation_id, external_message_id)` para idempotência (B5) |
| `events` | `id bigserial pk`, `conversation_id`, `type`, `payload jsonb`, `created_at` | Append-only; índice `(conversation_id, id)` |
| `quotes` | `id uuid pk`, `conversation_id`, `request jsonb`, `status`, `result jsonb`, `cache_key`, `created_at` | `status`: `succeeded`, `refused`, `invalid`, `unavailable` |
| `quote_attempts` | `id uuid pk`, `quote_id fk`, `attempt_no`, `status_code`, `error_kind`, `latency_ms`, `created_at` | Uma linha por chamada HTTP |
| `handoffs` | `id uuid pk`, `conversation_id`, `reason`, `ticket jsonb`, `status`, `created_at`, `resolved_at` | Usada no B4 |
| `pending_quotes` | `id uuid pk`, `conversation_id`, `request jsonb`, `attempts`, `next_attempt_at`, `deadline_at`, `status` | Fila do retry diferido (B4); índice em `(status, next_attempt_at)` |

## Critérios de aceite

- [ ] `docker compose up -d` sobe os três serviços; `postgres` fica `healthy`; `curl localhost:8000/health` responde.
- [ ] As migrações rodam do zero sem erro e de forma idempotente.
- [ ] `uv run pytest` passa, com testes dos normalizadores cobrindo os casos da tabela acima e as bordas (CEP com 7 dígitos, `"ano 05"`, `"29/02"`, data passada).
- [ ] A CI está verde no primeiro PR.
- [ ] Um engenheiro novo consegue subir o ambiente seguindo só a seção "Como rodar" do README (rascunho).

## Fora do escopo

- Lógica de cotação, LLM, grafo (B1–B3).
- Deploy em nuvem.

## Riscos e cuidados

- **Não modificar `quote-service/`.** O avaliador compara com o original; mudar a API seria mudar o problema.
- `testcontainers` precisa de Docker na CI: usar o serviço `postgres` do GitHub Actions como alternativa.
- O ano atual entra nos normalizadores: sempre receber `today` por parâmetro para os testes não quebrarem com o tempo.
