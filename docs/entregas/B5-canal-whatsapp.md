# B5 — Canal WhatsApp *(opcional)*

| Campo | Valor |
|---|---|
| Prioridade | P2 (primeiro a ser cortado) |
| Depende de | B3 (idealmente B4) |
| Desbloqueia | — |
| Critério da avaliação | Prontidão para produção (diferencial, não obrigatório) |
| Status | ☐ A fazer |

## Objetivo

Mostrar que o agente está pronto para um canal real: uma API HTTP que recebe mensagens no formato de um webhook de WhatsApp, lida com mensagens duplicadas e com rajadas, e responde pelo mesmo `Agent` usado no chat.

## Tarefas

- [ ] `api/app.py`: FastAPI com `POST /webhook/whatsapp`, `GET /conversations/{id}/messages`, `GET /healthz`.
- [ ] `channel/webhook.py`: `ChannelAdapter` que grava as respostas como mensagens de saída (o envio real fica num stub).
- [ ] `channel/debounce.py`: agrupamento de rajadas por conversa.
- [ ] Idempotência por `external_message_id`.
- [ ] `scripts/demo_webhook.sh`: sequência de `curl` que reproduz uma conversa completa.

## Especificação

### Entrada

```json
POST /webhook/whatsapp
X-Webhook-Secret: <segredo>
{
  "message_id": "wamid.ABC123",
  "from": "+5521972242584",
  "timestamp": "2026-11-02T14:03:00Z",
  "type": "text",
  "text": "oi, quero cotar meu carro"
}
```

- Formato simplificado, inspirado na Evolution API / WhatsApp Cloud API. Documentar o mapeamento no README.
- `type` ∈ `text | image | audio | document`; para mídia, vem `media_caption` em vez de `text`.
- Sem `X-Webhook-Secret` válido → 401.
- Responde **202 rápido** (< 100 ms); o processamento é assíncrono.

### Identidade da conversa

- `from` nunca é gravado. `external_ref_hash = sha256(sal + telefone)` localiza ou cria a conversa.
- Uma conversa com status final (encerrada ou handoff resolvido) não é reaberta: uma nova mensagem cria uma nova conversa.

### Idempotência

- `unique (conversation_id, external_message_id)` em `messages`.
- Mensagem repetida → 200 sem processar, evento `message_duplicate`.

### Agrupamento de rajadas (debounce)

- Ao chegar uma mensagem, espera `DEBOUNCE_MS` (padrão 2500 ms). Se chegarem outras da mesma conversa nesse intervalo, junta tudo num único turno.
- Implementação: um `asyncio.Task` por conversa, reiniciado a cada mensagem. Funciona com um processo; anotar no README que com várias réplicas precisaria de lock no Postgres ou de uma fila.
- Mídia no meio da rajada entra como marcador (`[áudio]`) e conta para a regra de mídia do B4.

### Saída

- O `ChannelAdapter` grava cada resposta em `messages` (`direction=out`) e chama `send()` de um stub que só loga. A troca pelo cliente real (Evolution API, Omni) fica isolada nesse adaptador.
- `GET /conversations/{id}/messages` lista a conversa (mascarada), para a demonstração.

## Critérios de aceite

- [ ] `scripts/demo_webhook.sh` produz a mesma conversa e a mesma cotação que o `autoseguro chat`.
- [ ] Enviar a mesma mensagem duas vezes gera só uma resposta.
- [ ] Três mensagens em 1 s ("oi" / "tenho 35" / "cep 01310-100") viram um turno só, com uma resposta.
- [ ] O telefone não aparece em nenhuma tabela nem log.
- [ ] O worker de cotação pendente (B4) entrega a cotação pelo mesmo canal.

## Testes

- [ ] `httpx.AsyncClient` + `ASGITransport`: 401 sem segredo; 202 normal; duplicata; mídia.
- [ ] Debounce com relógio controlado.
- [ ] Hash do telefone estável e com sal.

## Fora do escopo

- Envio real pelo WhatsApp; verificação de assinatura da Meta; filas externas.

## Riscos e cuidados

- Não deixar o webhook bloquear esperando o LLM ou a cotação: sempre 202 e processamento em segundo plano.
- O debounce em memória perde mensagens se o processo cair no meio da janela: aceitável aqui, registrar como limitação.
