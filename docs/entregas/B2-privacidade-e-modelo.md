# B2 — Privacidade e escolha do modelo

| Campo | Valor |
|---|---|
| Prioridade | P0 |
| Depende de | B0 |
| Desbloqueia | B3, B6 |
| Critério da avaliação | "Cuidado com dados sensíveis", qualidade das decisões |
| Status | ☐ A fazer |

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
- [ ] `safety/pii.py`: `PIIRedactor.redact(text) -> RedactionResult(text, entities)`.
- [ ] Processor do `structlog` que aplica o redator em todo log (segunda linha de defesa).
- [ ] Função `mask_cep_for_log("21310100") -> "21***-***"`.

### Silver mínima
- [ ] `data_pipeline/silver.py` (DuckDB): lê o parquet, ordena por `conversation_id, message_index`, mascara `message_body`, pseudonimiza `sender_name`, extrai o gabarito.
- [ ] Saída em `data/silver/lead_messages.parquet` (gerada por comando, fora do git).
- [ ] Comando `autoseguro data silver`.

### Modelo
- [ ] Serviço `ollama` no `docker-compose.yml` com perfil `local-llm` (veio do B0), com versão de imagem fixa, para rodar o `qwen3.5:9b` sem depender de provedor externo.
- [ ] `llm/factory.py`: `get_chat_model(settings, purpose)` → `ChatOpenAI` apontando para `LLM_BASE_URL` (com `max_retries=0`: o retry é nosso, numa camada só).
- [ ] `llm/extractor.py`: interface `Extractor.extract(state) -> Extraction`, com `LLMExtractor` (produção) e `ScriptedExtractor` (testes: devolve a extração escrita no YAML do cenário). Motivo: o `GenericFakeChatModel` do LangChain não suporta tool calling, então não dá para testar `with_structured_output` com ele.
- [ ] `llm/schemas.py`: `Extraction` (Pydantic), o formato de saída da extração.
- [ ] `llm/prompts/extraction.md`: prompt versionado.
- [ ] `eval/extraction_eval.py`: compara candidatos e gera `docs/eval/comparacao_modelos.md`.

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

- Amostra: 200 conversas da Silver (semente fixa). Entrada: as mensagens do lead até a que contém idade, ano e CEP.
- Candidatos: `Qwen/Qwen3.5-27B`, `Qwen/Qwen3.8-27B`, `openai/gpt-oss-20b` (confirmar disponibilidade no HF Inference Providers antes).
- Métricas por modelo: acurácia de `idade`, `veiculo_ano` e prefixo do CEP; % de saída inválida; latência p50/p95; custo estimado, se o provedor informar.
- Saída: `docs/eval/comparacao_modelos.md` com a tabela e a escolha justificada. Atualizar `LLM_MODEL` no `.env.example`.

## Critérios de aceite

- [ ] Testes do redator com pelo menos 30 casos (formatos válidos, inválidos, colados em outras palavras, vários na mesma frase), sem falso positivo em CEP, ano e valores em R$.
- [ ] Varredura da Silver inteira: **zero** ocorrências dos padrões de CPF, e-mail, telefone e placa em `body_redacted` (teste automatizado).
- [ ] Um log gerado com uma mensagem contendo CPF não mostra o CPF (teste do processor do `structlog`).
- [ ] `docs/eval/comparacao_modelos.md` existe, com a tabela dos candidatos e a escolha justificada.
- [ ] O modelo escolhido tem pelo menos 95% de acurácia em `idade` e `veiculo_ano`. Se nenhum chegar lá, registrar e ajustar o prompt antes de seguir.

## Testes

- [ ] Unitários do redator (tabela parametrizada).
- [ ] Propriedade: `redact(redact(x)) == redact(x)` (idempotência).
- [ ] Teste da Silver contra o parquet real (marcado `@pytest.mark.data`).
- [ ] Teste da fábrica: `LLM_PROVIDER=scripted` não faz chamada de rede.
- [ ] Teste de contrato do `LLMExtractor` com `respx` simulando a resposta do endpoint OpenAI-compatível (tool call válida, JSON inválido, timeout).

## Fora do escopo

- Presidio ou NER (citar como evolução no README).
- Silver completa e Gold (B6).

## Riscos e cuidados

- O provedor de inferência é um terceiro: isso é transferência de dados. Mitigação: só sai texto já mascarado e mínimo; o modo local (Ollama) elimina a transferência. Registrar em ADR.
- O CEP também tem prefixo de 2 dígitos parecido com DDD; garantir que o regex de telefone não engula o CEP.
- O free tier do HF tem limite de créditos: a comparação de 3 × 200 chamadas precisa caber nele (ou reduzir a amostra).
