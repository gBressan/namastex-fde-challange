# Avaliação da extração — Qwen 3.5 4B (Ollama, GPU local)

O agente usa o LLM só para **extrair** dados das mensagens do lead (o fluxo é decidido pelo código). Esta avaliação mede se o modelo escolhido faz isso bem o suficiente, usando o histórico de conversas como gabarito.

## Resultado

| Modelo | n | Idade | Ano | CEP | Erros | Retry de formato | p50 | p95 | Tokens in/out (média) | Custo |
|---|---|---|---|---|---|---|---|---|---|---|
| `qwen3.5:4b` | 200 | 100.0% | 100.0% | 100.0% | 0 | 1 | 2.41 s | 2.48 s | 1679 / 113 | US$ 0 |

Meta do card B2: **≥ 95%** em idade e ano. **Atingida.**

## Como foi medido

| Item | Valor |
|---|---|
| Amostra | 200 conversas da camada Silver, sorteio determinístico por hash (semente 42) |
| Entrada | Mensagens do lead **já mascaradas**, até a que traz idade/CEP e a seguinte |
| Gabarito | `lead_idade_informada`, ano de `veiculo_texto`, prefixo do CEP do texto original |
| Modelo | `qwen3.5:4b` (Q4_K_M, 3,3 GB) no Ollama `0.40.5`, em Docker |
| Hardware | NVIDIA RTX 4050 Laptop, 6 GB |
| Chamada | Function calling, temperatura 0, *thinking* desligado (`reasoning_effort: none`), 1 chamada por vez |
| Aquecimento | Uma chamada antes de medir (a primeira carrega o modelo na GPU e leva ~20 s) |

## O modelo rodou na GPU

Durante toda a rodada final (22:24:47 → 22:32:59), a cada 2 s:

| Fonte | Leitura |
|---|---|
| `ollama ps` (coluna `PROCESSOR`) | **191 de 191 leituras em `100% GPU`** |
| `nvidia-smi` — uso da GPU | média 86%, mediana 88% (só 2 leituras abaixo de 20%, no início e no fim) |
| `nvidia-smi` — VRAM | constante em 3.759 MiB |
| `nvidia-smi` — consumo | média de 72 W |

O `autoseguro doctor` mostra o mesmo: `Ollama com qwen3.5:4b carregado: 100% GPU`.

## Três rodadas, e o que cada uma ensinou

| Rodada | Resultado | O que apareceu | Correção |
|---|---|---|---|
| 1 | 199/200 | O único erro era **da ferramenta**, não do modelo: aquecimento e avaliação rodavam em dois event loops com o mesmo cliente HTTP (`Event loop is closed`) | Aquecimento e avaliação no mesmo event loop |
| 2 | 199/200 | **Erro real do modelo**: devolveu `plano_id` como o texto `"None"` nas duas tentativas | `"None"`, `"null"` e texto vazio passam a ser tratados como "não informado" no formato da extração |
| 3 | **200/200** | 1 conversa precisou da segunda tentativa de formato, que o extrator já faz | — |

Nas rodadas 1 e 2, todas as conversas que o modelo respondeu estavam certas.

## Por que este modelo

- **Custo zero e sem chave:** a conta gratuita do Hugging Face não tem créditos mensais para Inference Providers (a chamada devolveu 402), e o objetivo é fazer o desafio sem custo. Quem avaliar o projeto também não precisa de chave nenhuma.
- **Cabe inteiro na GPU disponível:** 3,1 GB carregado, numa placa de 6 GB. O `qwen3.5:9b` (6,6 GB) não caberia inteiro e rodaria parte na CPU.
- **Já atinge a meta:** 100% na tarefa que o LLM tem no agente.
- **Trocável:** o código só fala a API da OpenAI. Para usar uma API paga (por exemplo, `Qwen/Qwen3.5-27B` pelo router do Hugging Face), basta mudar `LLM_PROVIDER`, `LLM_BASE_URL` e `LLM_MODEL`.

## Limitações

- **O dataset é sintético e regular:** os leads escrevem idade, ano e CEP em poucos formatos. Mensagens reais seriam mais variadas; 100% aqui não significa 100% em produção.
- **Só mede idade, ano e CEP.** Intenções (pedir humano, fechar, negociar) não têm gabarito no dataset; elas são testadas nos cenários do B4 e na simulação do B6.
- **A latência depende da máquina.** Sem GPU, o mesmo modelo roda na CPU, bem mais devagar.
- **Comparação com outros modelos não foi feita:** a escolha do 4B foi do autor (cabe na GPU e custa zero), e esta avaliação confirma que ele atende.

## Como reproduzir

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
cd agent
uv run autoseguro data silver
uv run autoseguro eval extraction --model qwen3.5:4b --n 200 --concurrency 1
docker compose exec ollama ollama ps   # PROCESSOR: 100% GPU
```

Os resultados por conversa ficam em `data/eval/extraction_qwen3.5--4b.json` (fora do git).
