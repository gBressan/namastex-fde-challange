"""Compara modelos na tarefa real de extração, usando a camada Silver como gabarito.

Para cada conversa da amostra, o extrator recebe as mensagens do lead (já mascaradas) até a
que traz idade e CEP, e a resposta é comparada com `gt_idade`, `gt_veiculo_ano` e
`gt_cep_prefixo`.
"""

from __future__ import annotations

import asyncio
import contextlib
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import duckdb

from autoseguro.domain.normalizers import normalize_cep
from autoseguro.llm.extractor import ExtractionDetails, ExtractionInput

# Preço em US$ por milhão de tokens no provedor DeepInfra, via router do Hugging Face
# (consultado em https://router.huggingface.co/v1/models em 31/10/2026).
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "Qwen/Qwen3.5-27B": (0.26, 2.60),
    "Qwen/Qwen3.8-27B": (0.20, 2.50),
    "Qwen/Qwen3.5-9B": (0.10, 0.15),
}
# Modelos locais (Ollama) não têm custo por token.


@dataclass(frozen=True)
class Sample:
    conversation_id: str
    lead_messages: list[str]
    gt_idade: int
    gt_veiculo_ano: int
    gt_cep_prefixo: str


@dataclass(frozen=True)
class SampleResult:
    conversation_id: str
    ok_idade: bool
    ok_veiculo_ano: bool
    ok_cep: bool
    attempts: int
    latency_s: float
    input_tokens: int
    output_tokens: int
    error: str | None = None


class DetailedExtractor(Protocol):
    async def extract_with_details(self, data: ExtractionInput) -> ExtractionDetails: ...


def load_samples(silver: Path, n: int, seed: int = 42) -> list[Sample]:
    """Sorteia `n` conversas (sempre as mesmas para a mesma semente)."""
    sql = f"""
    WITH s AS (SELECT * FROM read_parquet(?)),
    cut AS (
        SELECT conversation_id,
               min(message_index) FILTER (WHERE body_redacted LIKE '%[CPF]%') AS idx
        FROM s GROUP BY conversation_id
    ),
    -- Sorteio por hash, não por USING SAMPLE: o REPEATABLE do DuckDB só é determinístico
    -- com uma thread, e a amostra precisa ser a mesma em toda execução.
    picked AS (
        SELECT conversation_id FROM cut
        ORDER BY hash(conversation_id || ':' || {int(seed)})
        LIMIT {int(n)}
    )
    SELECT s.conversation_id,
           list(s.body_redacted ORDER BY s.message_index),
           any_value(s.gt_idade), any_value(s.gt_veiculo_ano), any_value(s.gt_cep_prefixo)
    FROM s JOIN cut USING (conversation_id) JOIN picked USING (conversation_id)
    -- Até a mensagem com idade/CEP/CPF e a seguinte (e-mail e telefone, quando vêm).
    WHERE s.message_index <= cut.idx + 1
    GROUP BY s.conversation_id
    ORDER BY s.conversation_id
    """
    con = duckdb.connect()
    try:
        rows = con.execute(sql, [str(silver)]).fetchall()
    finally:
        con.close()
    return [Sample(r[0], list(r[1]), r[2], r[3], r[4]) for r in rows]


async def warm_up(extractor: DetailedExtractor) -> None:
    """Carrega o modelo antes de medir. Erro aqui não importa: a avaliação registra os seus."""
    with contextlib.suppress(Exception):
        await extractor.extract_with_details(ExtractionInput(lead_messages=["oi"]))


async def evaluate(
    extractor: DetailedExtractor, samples: list[Sample], *, concurrency: int = 4
) -> list[SampleResult]:
    semaphore = asyncio.Semaphore(concurrency)

    async def run(sample: Sample) -> SampleResult:
        async with semaphore:
            try:
                details = await extractor.extract_with_details(
                    ExtractionInput(lead_messages=sample.lead_messages)
                )
            except Exception as exc:  # erro de rede, timeout ou saída inválida
                return SampleResult(
                    sample.conversation_id, False, False, False, 0, 0.0, 0, 0,
                    error=f"{type(exc).__name__}: {str(exc)[:200]}",
                )  # fmt: skip
        e = details.extraction
        cep = normalize_cep(e.cep)
        return SampleResult(
            conversation_id=sample.conversation_id,
            ok_idade=e.idade == sample.gt_idade,
            ok_veiculo_ano=e.veiculo_ano == sample.gt_veiculo_ano,
            ok_cep=cep is not None and cep[:2] == sample.gt_cep_prefixo,
            attempts=details.attempts,
            latency_s=details.latency_s,
            input_tokens=details.input_tokens,
            output_tokens=details.output_tokens,
        )

    return list(await asyncio.gather(*(run(s) for s in samples)))


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, round(pct * len(ordered)) - 1)] if ordered else 0.0


def summarize(model: str, results: list[SampleResult]) -> dict[str, Any]:
    n = len(results)
    answered = [r for r in results if r.error is None]
    latencies = [r.latency_s for r in answered]
    tokens_in = sum(r.input_tokens for r in results)
    tokens_out = sum(r.output_tokens for r in results)
    price_in, price_out = PRICES_PER_MTOK.get(model.split(":")[0], (0.0, 0.0))
    return {
        "model": model,
        "n": n,
        "acc_idade": sum(r.ok_idade for r in results) / n,
        "acc_veiculo_ano": sum(r.ok_veiculo_ano for r in results) / n,
        "acc_cep": sum(r.ok_cep for r in results) / n,
        "erros": n - len(answered),
        "retry_formato": sum(r.attempts > 1 for r in answered),
        "latencia_p50_s": statistics.median(latencies) if latencies else 0.0,
        "latencia_p95_s": _percentile(latencies, 0.95),
        "tokens_in_medio": tokens_in / n,
        "tokens_out_medio": tokens_out / n,
        "custo_usd": (tokens_in * price_in + tokens_out * price_out) / 1_000_000,
    }


def results_to_json(results: list[SampleResult]) -> list[dict[str, Any]]:
    return [asdict(r) for r in results]


def render_markdown(summaries: list[dict[str, Any]]) -> str:
    header = (
        "| Modelo | n | Idade | Ano | CEP | Erros | Retry de formato | p50 | p95 "
        "| Tokens in/out (média) | Custo |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    rows = "".join(
        f"| `{s['model']}` | {s['n']} | {s['acc_idade']:.1%} | {s['acc_veiculo_ano']:.1%} "
        f"| {s['acc_cep']:.1%} | {s['erros']} | {s['retry_formato']} "
        f"| {s['latencia_p50_s']:.2f} s | {s['latencia_p95_s']:.2f} s "
        f"| {s['tokens_in_medio']:.0f} / {s['tokens_out_medio']:.0f} | US$ {s['custo_usd']:.4f} |\n"
        for s in summaries
    )
    return header + rows
