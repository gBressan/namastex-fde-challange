# AutoSeguro — agente de cotação de seguro auto

Agente que atende leads de seguro auto pelo WhatsApp: conversa, qualifica, cota o plano na
API de cotação (instável de propósito) e decide quando resolve sozinho e quando passa para
um humano.

> 🚧 **Em construção.** O andamento está no [quadro de entregas](docs/entregas/README.md).
> O enunciado original do desafio está em [`docs/DESAFIO.md`](docs/DESAFIO.md).

## Como rodar

### Pré-requisitos

- **Docker** (com Docker Compose). É obrigatório: além da API de cotação, o agente guarda
  estado, eventos e a fila de cotações pendentes no Postgres.
- **[uv](https://docs.astral.sh/uv/)**, para rodar o agente e os testes fora do container.

### Subir o ambiente

```bash
cp .env.example .env          # depois preencha LLM_API_KEY
docker compose up -d --build  # quote-api + postgres + migrações
docker compose run --rm agent autoseguro doctor
```

O `doctor` confere se o Postgres e a API de cotação estão acessíveis. Os serviços:

| Serviço | Porta | O que é |
|---|---|---|
| `quote-api` | 8000 | API de cotação do desafio, sem alterações |
| `postgres` | 5432 (`POSTGRES_PORT`) | Estado das conversas, eventos, cotações, handoffs |
| `migrate` | — | Aplica as migrações e termina |
| `agent` | — | CLI do agente (perfil `cli`, usado com `docker compose run`) |

### Rodar o agente fora do container

```bash
cd agent
uv sync
uv run autoseguro db migrate
uv run autoseguro doctor
```

### Testes e qualidade

```bash
cd agent
uv run pytest -m "not chaos"   # banco e API usam o compose (os testes pulam se não houver)
                              # os testes `data` leem o dataset/ do repositório
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

### Cotar direto na API (sem LLM)

O cliente da cotação faz timeout, retry com backoff e circuit breaker, e grava cada tentativa
no Postgres. Para ver isso funcionando:

```bash
cd agent
uv run autoseguro quote --plano completo --idade 35 --ano 2022 --cep 21310-100 --inicio 15/11
# 50 cotações seguidas, sem cache, com resumo de tempos e desfechos
uv run autoseguro quote --plano completo --idade 35 --ano 2022 --cep 01310-100 --repeat 50 --no-cache
```

### Dados e modelo de LLM

```bash
cd agent
# Camada Silver: mensagens de lead mascaradas, com gabarito (grava em data/silver/)
uv run autoseguro data silver
# Compara modelos na extração (precisa de token do Hugging Face com créditos)
uv run autoseguro eval extraction --model Qwen/Qwen3.5-27B:deepinfra --n 30
```

O token do LLM vem de `LLM_API_KEY`, de `HF_TOKEN` ou do login do `hf` CLI
(`hf auth login`). Dentro do Docker, use `LLM_API_KEY` ou `HF_TOKEN` no `.env`.

### Simular falhas da API de cotação

```bash
# API fora do ar (100% de erro 5xx)
docker compose -f docker-compose.yml -f docker-compose.chaos-down.yml up -d quote-api
# API lenta (100% das chamadas demoram 8 s)
docker compose -f docker-compose.yml -f docker-compose.chaos-slow.yml up -d quote-api
# Voltar ao comportamento padrão (20% de falha, 10% de lentidão)
docker compose up -d quote-api
```

Com a API em um desses modos, os testes de caos conferem o comportamento do cliente:

```bash
cd agent
CHAOS_MODE=down uv run pytest -m chaos   # ou CHAOS_MODE=slow
```

## Estrutura

```
agent/                 pacote Python `autoseguro` (agente, CLI, migrações, testes)
quote-service/         API de cotação do desafio (intocada)
dataset/               histórico de conversas do desafio (intocado)
scripts/               gerador do dataset (intocado)
docs/                  proposta de arquitetura, cards de entrega, enunciado do desafio
```

O primeiro commit deste repositório importa o desafio exatamente como está no commit `52a006c`
do [repositório da Namastex](https://github.com/namastexlabs/namastex-fde-challenge).
`quote-service/`, `dataset/` e `scripts/` seguem idênticos a ele: a CI busca esse commit no
GitHub da Namastex e compara a cada push.

## Documentação

- [Proposta de arquitetura](docs/PROPOSTA_ARQUITETURA.md): decisões e o porquê de cada uma.
- [Cards de entrega](docs/entregas/README.md): o que cada bloco entrega e como é verificado.
