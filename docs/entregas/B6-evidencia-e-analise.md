# B6 — Evidência e análise

| Campo | Valor |
|---|---|
| Prioridade | P1 |
| Depende de | B2 (Silver mínima), B4 (agente completo) |
| Desbloqueia | B7 (números do README) |
| Critério da avaliação | Qualidade, uso do dataset ("use como bem entender") |
| Status | ☐ A fazer |

## Objetivo

Transformar "funciona" em **números**: o que o dataset revela sobre o processo de vendas e quão bem o agente se sai em muitas conversas, com o caos ligado.

## Tarefas

### Silver completa e Gold
- [ ] Estender `data_pipeline/silver.py` para todas as mensagens e colunas abaixo.
- [ ] `data_pipeline/gold.py`: tabelas agregadas.
- [ ] `docs/analise_dataset.md`: relatório curto, com tabelas (e gráficos, se ajudarem).
- [ ] Comando `autoseguro data gold`.

### Simulação em lote
- [ ] `eval/lead_simulator.py`: lead simulado por LLM, com persona tirada da Silver.
- [ ] `eval/run_simulation.py`: roda N conversas contra o agente real (API de cotação real, caos padrão, `QUOTE_SEED` fixo).
- [ ] `eval/metrics.py`: calcula as métricas a partir do banco (`events`, `quotes`, `quote_attempts`, `handoffs`).
- [ ] `docs/eval/simulacao.md`: resultado.

## Especificação

### Silver completa — colunas extras

| Coluna | Como |
|---|---|
| `is_media` | `message_type != 'text'` |
| `veiculo_marca`, `veiculo_modelo`, `veiculo_ano` | Parse de `veiculo_texto` |
| `cep_prefixo`, `cep_alto_risco` | Regex no texto original (antes de mascarar), só o prefixo é guardado |
| `vendedor_plano_ofertado`, `vendedor_preco_ofertado` | Regex na mensagem "Pelo perfil consigo o plano X por R$ Y" |
| `preco_correto` | Oráculo (`tests/oracle.py`) com idade, ano, CEP e plano ofertado, na data da conversa |
| `elegivel` | Idade 18–75 e veículo ≤ 20 anos na data da conversa |

### Gold — perguntas a responder

| Pergunta | Por que importa |
|---|---|
| Em quantas conversas o vendedor ofereceu preço diferente do correto? Qual o desvio médio? | Mostra por que o agente deve usar a API e nunca o histórico |
| Quantos leads eram inelegíveis (idade ou veículo) e mesmo assim receberam oferta, ou "fecharam"? | Risco operacional que o agente elimina |
| Conversão (`ganho`) por plano, faixa etária e região de risco | Contexto de negócio |
| Frequência de cada objeção e o desfecho de cada uma | Justifica as respostas de objeção do B4 |
| % de conversas com mídia (áudio, documento, imagem) | Justifica a regra de mídia |
| % de leads que mandam CPF, e-mail ou telefone sem ser pedido | Justifica o redator |

### Simulação

- **Persona** de cada lead simulado: idade, veículo, CEP, se tem objeção (qual), se vai mandar mídia, se pede humano, tirados da Silver (sem PII).
- O lead simulado recebe só as mensagens do agente e responde em PT-BR informal; limite de 15 turnos.
- N = 50 conversas (ajustar ao limite de créditos do provedor). Caos padrão (20%/10%) com `QUOTE_SEED`.

### Métricas

| Métrica | Definição | Meta |
|---|---|---|
| Cotações corretas | % de `quote_succeeded` cujo prêmio = oráculo | 100% |
| Preços inventados | Valores em R$ nas mensagens de saída que não vieram da API | **0** |
| Bloqueios do guardrail | Contagem de `guardrail_blocked` | Reportar |
| Conversas que chegaram à cotação | % com `quote_succeeded`, entre as elegíveis | Reportar |
| Handoff por motivo | Distribuição de `reason` | Comparar com a persona (ex.: quem pediu humano recebeu handoff?) |
| Handoff correto | % de handoffs cujo motivo bate com o comportamento da persona | ≥ 90% |
| Tentativas por cotação | Média e máximo de `quote_attempts` | Reportar |
| Latência da cotação | p50 / p95 / máx (do `quote_requested` ao resultado) | p95 ≤ 12 s |
| Turnos até a cotação | Mediana | Reportar |
| Extração degradada | % de turnos com `llm_degraded` | Reportar |

## Critérios de aceite

- [ ] `docs/analise_dataset.md` responde às perguntas da Gold, com números.
- [ ] `docs/eval/simulacao.md` traz a tabela de métricas, a configuração usada (modelo, N, semente, taxas de caos) e 2 ou 3 conversas de exemplo comentadas (uma boa, uma que falhou e por quê).
- [ ] As métricas são reproduzíveis com um comando documentado.
- [ ] Preços inventados = 0. Se não for, o card não fecha: corrigir antes.

## Testes

- [ ] Parse de `veiculo_texto` e da oferta do vendedor (casos do gerador).
- [ ] `metrics.py` com um banco de teste preenchido à mão (resultado conhecido).

## Fora do escopo

- Dashboard; avaliação com LLM-juiz da qualidade do texto (pode entrar como próximo passo).

## Versão mínima, se faltar tempo

Só a análise do dataset (Silver completa + perguntas da Gold), sem a simulação. As métricas do agente ficam com os cenários golden do B4.

## Riscos e cuidados

- O lead simulado pode "colaborar demais". Incluir personas difíceis (respostas vagas, mudança de ideia, mídia).
- O custo de LLM dobra (agente + lead simulado). Usar um modelo menor para o lead simulado.
