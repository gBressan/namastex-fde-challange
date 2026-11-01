# B2 — Privacidade e escolha do modelo

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Depende de | B0 |
| Desbloqueia | B3, B6 |
| Critério da avaliação | "Cuidado com dados sensíveis", qualidade das decisões |
| Status | ☑ Pronto (merge na main em 31/10/2026) |

## Objetivo

1. Garantir que nenhum dado pessoal chegue a logs, eventos ou prompts.
2. Ter uma camada Silver mínima do dataset, já mascarada.
3. Escolher o modelo de LLM **com números**, comparando candidatos na tarefa real de extração.

## Contexto

- Os leads mandam CPF, e-mail, telefone e placa soltos no texto (ver `dataset/DICIONARIO.md`).
- **A cotação não precisa de nada disso.** Pelo princípio da necessidade da LGPD (art. 6º, III), o agente não pede e não guarda esses dados.
- O CEP é dado de negócio (entra na cotação), mas nos logs aparece só o prefixo.
- O gerador do dataset diz que a PII deve ser mascarada "na camada Silver".

## Tarefas

### Mascaramento
- [x] `safety/pii.py`: `PIIRedactor.redact(text) -> RedactionResult(text, entities)`.
- [x] Processor do `structlog` que aplica o redator em todo log (segunda linha de defesa).
- [x] Função `mask_cep_for_log("21310100") -> "21***-***"` (já existia desde o B0, em `domain/normalizers.py`).

### Silver mínima
- [x] `autoseguro/data/silver.py` (DuckDB): lê o parquet, ordena por `conversation_id, message_index`, mascara `message_body`, pseudonimiza `sender_name`, extrai o gabarito. *Dentro do pacote (e não em `data_pipeline/` na raiz) para o CLI importar direto. O DuckDB exige `numpy` para registrar o redator como função SQL; a dependência foi adicionada.*
- [x] Saída em `data/silver/lead_messages.parquet` (gerada por comando, fora do git).
- [x] Comando `autoseguro data silver` (gera, mostra quantos dados foram mascarados e varre o resultado atrás de vazamentos).

### Modelo
- [x] Serviço `ollama` no `docker-compose.yml` (imagem `ollama/ollama:0.40.5`), com `ollama-pull` para baixar o modelo uma vez e `docker-compose.gpu.yml` para dar acesso à GPU NVIDIA.
  - *Histórico da decisão (31/10/2026):* primeiro o Ollama foi descartado em favor de usar só a API do Hugging Face. A conta gratuita do HF não tem créditos (402), e o objetivo é fazer o desafio sem custo; por isso a decisão final é o **`qwen3.5:4b` local**, que cabe inteiro na RTX 4050 (6 GB). Trocar para uma API é só configuração.
- [x] `llm/ollama.py` + `autoseguro doctor`: mostram se o modelo está baixado, carregado e **quanto dele está na GPU** (`100% GPU`).
- [x] `llm/factory.py`: `get_chat_model(settings)` → `ChatOpenAI` com `max_retries=0` (o retry é nosso, numa camada só). Provedor `ollama` (padrão: endpoint local, sem token, *thinking* desligado com `reasoning_effort: none`) ou `openai_compatible` (ex.: router do HF; token de `LLM_API_KEY`, `HF_TOKEN` ou login do `hf` CLI; *thinking* desligado com `chat_template_kwargs`).
- [x] `llm/extractor.py`: interface `Extractor.extract(ExtractionInput) -> Extraction`, com `LLMExtractor` (produção) e `ScriptedExtractor` (testes: devolve a extração escrita no YAML do cenário). Motivo: o `GenericFakeChatModel` do LangChain não suporta tool calling, então não dá para testar `with_structured_output` com ele.
- [x] `llm/schemas.py`: `Extraction` (Pydantic), o formato de saída da extração.
- [x] `llm/prompts/extraction.md`: prompt versionado.
- [x] `autoseguro/eval/extraction.py` + comando `autoseguro eval extraction`: compara candidatos e gera `docs/eval/comparacao_modelos.md`.
- [x] **Rodar a avaliação** com o `qwen3.5:4b` local: **100% em idade, ano e CEP nas 200 conversas**, 0 erros. Relatório em [`docs/eval/comparacao_modelos.md`](../eval/comparacao_modelos.md).

## Especificação

### Entidades mascaradas

| Entidade | Padrões aceitos | Substituto | Observação |
|---|---|---|---|
| CPF | `389.083.863-43`, `38908386343`, `389 083 863 43` | `[CPF]` | Valida dígito verificador; um número com formato de CPF mas inválido também é mascarado (marcado `valid=false`) |
| Telefone | `+55 21 97224-2584`, `(21) 97224-2584`, `21972242584` | `[TELEFONE]` | Fixo e celular |
| E-mail | RFC simplificado | `[EMAIL]` | |
| Placa | `ABC1D23` (Mercosul), `ABC-1234`, `ABC1234` | `[PLACA]` | Maiúsculas e minúsculas |
| Nome do remetente | `sender_name` | `lead_<hash8>` / `vendedor_<hash8>` | Só no dataset (pseudonimização com hash + sal) |

- `RedactionResult.entities` guarda **tipo e posição**, nunca o valor original.
- O CEP **não** é mascarado no texto que vai para a extração (é necessário para cotar), mas é mascarado nos logs.
- Ordem no fluxo de entrada: chega a mensagem → **redator** → grava `body_redacted` → vai para o LLM. O texto original não é gravado em lugar nenhum.
- Se o lead mandar CPF espontaneamente, o agente responde que não precisa dele para cotar (tratado no B3/B4) e o evento `pii_discarded` registra só o tipo.

### Silver mínima — colunas

| Coluna | Origem |
|---|---|
| `conversation_id`, `message_index`, `sender_role`, `message_type` | Originais |
| `sender_pseudonym` | Hash de `sender_name` |
| `body_redacted` | `message_body` após o redator |
| `gt_idade` | `lead_idade_informada` |
| `gt_veiculo_ano` | Últimos 4 dígitos de `veiculo_texto` |
| `gt_cep_prefixo` | Regex no texto original, antes de mascarar (só os 2 primeiros dígitos) |

### Formato da extração

```python
class Intent(StrEnum):
    INFORMAR_DADOS = "informar_dados"; CONFIRMAR = "confirmar"; CORRIGIR = "corrigir"
    PEDIR_HUMANO = "pedir_humano"; ACEITAR_PROPOSTA = "aceitar_proposta"
    NEGOCIAR_PRECO = "negociar_preco"; OBJECAO_FRANQUIA = "objecao_franquia"
    OBJECAO_PENSAR = "objecao_pensar"; DUVIDA = "duvida"; FORA_ESCOPO = "fora_escopo"
    SAUDACAO = "saudacao"; DESPEDIDA = "despedida"; OUTRO = "outro"

class Extraction(BaseModel):
    intent: Intent
    veiculo_descricao: str | None = None
    veiculo_ano: int | None = None
    idade: int | None = None
    cep: str | None = None
    plano_id: PlanId | None = None
    data_inicio_texto: str | None = None   # texto cru; o parse é feito por código (B0)
    menciona_concorrente: bool = False
    confidence: float = Field(ge=0, le=1)
```

- O LLM **extrai**, o código **normaliza e valida** (normalizadores do B0). A data, por exemplo, volta como texto e é convertida pelo `parse_data_inicio`.
- Chamada com `with_structured_output(Extraction, method="function_calling")`, temperatura 0, *thinking* desligado (`extra_body={"chat_template_kwargs": {"enable_thinking": False}}`, quando o modelo suportar).
- Se o parse falhar: uma nova tentativa com a mensagem de erro de validação; se falhar de novo, cai no extrator por regex (B4).
- Few-shot: **somente mensagens de lead** da Silver. Nunca falas de vendedor (elas têm preços e coberturas errados).

### Comparação de modelos

- Amostra: 200 conversas da Silver (semente fixa). Entrada: as mensagens do lead até a que contém idade, ano e CEP (e a seguinte).
- O sorteio é feito por `ORDER BY hash(conversation_id || seed)`: o `USING SAMPLE ... REPEATABLE` do DuckDB devolveu amostras diferentes com a mesma semente (só é determinístico com uma thread).
- Todos os candidatos rodam no **mesmo provedor** (DeepInfra, que tem os três com tool calling e saída estruturada), para a comparação medir só o modelo. Preços em US$/milhão de tokens consultados no router em 31/10/2026: Qwen3.5-27B 0,26/2,60; Qwen3.8-27B 0,20/2,50; Qwen3.5-9B 0,10/0,15.
- Modelo avaliado: `qwen3.5:4b` no Ollama local (decisão do autor: custo zero e cabe na GPU). Os candidatos do HF (`Qwen3.5-27B`, `Qwen3.8-27B`, `Qwen3.5-9B`) ficaram de fora por falta de créditos; o comando `autoseguro eval extraction` aceita qualquer um deles se a conta tiver saldo.
- Métricas por modelo: acurácia de `idade`, `veiculo_ano` e prefixo do CEP; % de saída inválida; latência p50/p95; custo estimado, se o provedor informar.
- Saída: `docs/eval/comparacao_modelos.md` com a tabela e a escolha justificada. Atualizar `LLM_MODEL` no `.env.example`.

## Critérios de aceite

- [x] Testes do redator com pelo menos 30 casos (formatos válidos, inválidos, colados em outras palavras, vários na mesma frase), sem falso positivo em CEP, ano e valores em R$. *(47 testes em `tests/unit/test_pii.py`, incluindo 500 UUIDs aleatórios que não podem virar telefone ou CPF.)*
- [x] Varredura da Silver inteira: **zero** ocorrências dos padrões de CPF, e-mail, telefone e placa em `body_redacted` (teste automatizado). *(16.470 mensagens de 2.500 conversas; mascarados 2.500 CPFs, 1.379 telefones, 1.379 e-mails e 839 placas, batendo com as contagens do dataset. Além do redator, uma checagem com regex independentes.)*
- [x] Um log gerado com uma mensagem contendo CPF não mostra o CPF (teste do processor do `structlog`).
- [x] `docs/eval/comparacao_modelos.md` existe, com a tabela, a escolha justificada e as limitações.
- [x] O modelo escolhido tem pelo menos 95% de acurácia em `idade` e `veiculo_ano`. *(100% em idade, ano e CEP; p50 de 2,41 s e p95 de 2,48 s por extração.)*
- [x] O modelo roda na GPU. *(Na rodada final, 191 de 191 leituras do `ollama ps` em `100% GPU`; `nvidia-smi` com uso médio de 86% e VRAM constante em 3.759 MiB.)*

### Achados da validação

- **Primeira chamada lenta:** carregar o modelo na GPU leva ~20 s e estourou o timeout. A avaliação faz uma chamada de aquecimento antes de medir, e o Ollama mantém o modelo carregado por 30 min (`OLLAMA_KEEP_ALIVE`). *No agente (B3), isso vira um aquecimento na inicialização.*
- **Bug da ferramenta (rodada 1):** aquecimento e avaliação em dois event loops com o mesmo cliente HTTP → `Event loop is closed`. Corrigido.
- **Vício do modelo (rodada 2):** `plano_id` devolvido como o texto `"None"` nas duas tentativas. Agora `"None"`, `"null"` e texto vazio são tratados como "não informado".
- **`tool_choice`:** a documentação do Ollama diz que o endpoint compatível não aceita `tool_choice`, mas o function calling funcionou em 199 de 200 conversas na primeira tentativa (e na segunda na restante). Não foi preciso trocar para JSON mode.

## Testes

- [x] Unitários do redator (tabela parametrizada).
- [x] Propriedade: `redact(redact(x)) == redact(x)` (idempotência).
- [x] Teste da Silver contra o parquet real (marcado `@pytest.mark.data`).
- [x] Ordem do token: `LLM_API_KEY` → `HF_TOKEN` → login do `hf` CLI; sem nenhum, erro com instrução clara.
- [x] Teste de contrato do `LLMExtractor` simulando o endpoint OpenAI-compatível (tool call válida, saída inválida corrigida na 2ª tentativa, saída sempre inválida, 503 sem retry do cliente). *O SDK da OpenAI 3.x usa `httpx2`, que o `respx` não intercepta: o endpoint falso entra como `httpx2.MockTransport` no cliente HTTP do modelo.*

## Fora do escopo

- Presidio ou NER (citar como evolução no README).
- Silver completa e Gold (B6).

## Riscos e cuidados

- O provedor de inferência é um terceiro: isso é transferência de dados. Mitigação: só sai texto já mascarado e mínimo. Registrar em ADR.
- O CEP também tem prefixo de 2 dígitos parecido com DDD; garantir que o regex de telefone não engula o CEP.
- O free tier do HF tem limite de créditos. **Aconteceu:** a conta gratuita não tem créditos mensais (402). Resolvido com o modelo local no Ollama.
- Sem GPU NVIDIA, o modelo roda na CPU: funciona, mas bem mais devagar. O README explica as duas formas de subir.
