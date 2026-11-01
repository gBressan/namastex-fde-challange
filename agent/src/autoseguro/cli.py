"""Linha de comando `autoseguro`."""

from __future__ import annotations

import io
import json
import statistics
import sys
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import httpx
import psycopg
import typer
from rich.console import Console

from autoseguro import db
from autoseguro.config import Settings, get_settings
from autoseguro.data.silver import DEFAULT_DATASET, DEFAULT_SILVER, build_silver, find_leaks
from autoseguro.domain.models import PlanId, QuoteRequest
from autoseguro.domain.normalizers import normalize_cep, parse_data_inicio
from autoseguro.eval.extraction import (
    Sample,
    SampleResult,
    evaluate,
    load_samples,
    render_markdown,
    results_to_json,
    summarize,
    warm_up,
)
from autoseguro.llm.extractor import LLMExtractor
from autoseguro.llm.factory import get_chat_model, resolve_api_key
from autoseguro.llm.ollama import ollama_status
from autoseguro.log import configure_logging
from autoseguro.quote.cache import QuoteCache
from autoseguro.quote.client import QuoteClient, QuoteClientConfig
from autoseguro.quote.outcomes import (
    QuoteInvalid,
    QuoteOutcome,
    QuoteRefused,
    QuoteSucceeded,
    QuoteUnavailable,
)
from autoseguro.store.base import EventStore, QuoteRepository
from autoseguro.store.memory import InMemoryEventStore, InMemoryQuoteRepository
from autoseguro.store.postgres import PostgresEventStore, PostgresQuoteRepository

app = typer.Typer(help="Agente de cotação AutoSeguro.", no_args_is_help=True)
db_app = typer.Typer(help="Banco de dados (Postgres).", no_args_is_help=True)
app.add_typer(db_app, name="db")

console = Console()


@app.callback()
def _setup() -> None:
    # Consoles do Windows ainda usam cp1252: um caractere fora dele não pode derrubar o CLI.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)


@db_app.command("migrate")
def db_migrate() -> None:
    """Aplica as migrações pendentes (idempotente)."""
    db.migrate(get_settings())
    console.print("[green]Migrações aplicadas.[/green]")


@app.command()
def doctor() -> None:
    """Confere se Postgres e quote-api estão acessíveis com a configuração atual."""
    settings = get_settings()
    ok = True

    try:
        version = db.ping(settings)
        console.print(f"[green]✓[/green] Postgres {version}")
    except psycopg.OperationalError as exc:
        ok = False
        console.print(f"[red]✗[/red] Postgres inacessível: {exc}")

    # /health da quote-api responde sempre: confirma que o serviço está de pé, não que a
    # /quote está funcionando (ela falha de propósito).
    try:
        response = httpx.get(f"{settings.quote_api_url}/health", timeout=3)
        response.raise_for_status()
        console.print(f"[green]✓[/green] quote-api em {settings.quote_api_url}")
    except httpx.HTTPError as exc:
        ok = False
        console.print(f"[red]✗[/red] quote-api inacessível: {exc}")

    if settings.llm_provider == "ollama":
        try:
            status = ollama_status(settings)
            if not status.downloaded:
                ok = False
                console.print(
                    f"[red]✗[/red] Ollama no ar, mas {status.model} não foi baixado "
                    "(docker compose up ollama-pull)"
                )
            elif not status.loaded:
                console.print(
                    f"[green]✓[/green] Ollama com {status.model} baixado "
                    "(ainda não carregado; carrega na primeira chamada)"
                )
            else:
                gpu = status.gpu_fraction or 0.0
                where = "100% GPU" if gpu >= 0.999 else f"{gpu:.0%} GPU / {1 - gpu:.0%} CPU"
                console.print(f"[green]✓[/green] Ollama com {status.model} carregado: {where}")
        except httpx.HTTPError as exc:
            ok = False
            console.print(f"[red]✗[/red] Ollama inacessível em {settings.ollama_base_url}: {exc}")
    else:
        llm_key = "encontrada" if resolve_api_key(settings) else "[yellow]não encontrada[/yellow]"
        console.print(f"  LLM: {settings.llm_model} via {settings.llm_base_url} (chave {llm_key})")

    if not ok:
        raise typer.Exit(code=1)


# --- autoseguro quote ---------------------------------------------------------------------


class _PrintingEventStore:
    """Repassa os eventos para o store real e mostra as tentativas no terminal."""

    def __init__(self, inner: EventStore, *, quiet: bool) -> None:
        self._inner = inner
        self._quiet = quiet

    async def append(
        self, type: str, payload: Mapping[str, Any], *, conversation_id: UUID | None = None
    ) -> None:
        await self._inner.append(type, payload, conversation_id=conversation_id)
        if self._quiet:
            return
        if type == "quote_attempt":
            status = payload["status_code"] or "-"
            kind = payload["error_kind"] or "ok"
            console.print(
                f"  tentativa {payload['attempt_no']}: HTTP {status} ({kind}) "
                f"em {payload['latency_ms']} ms"
            )
        elif type == "breaker_state_changed":
            console.print(f"  [yellow]breaker: {payload['from']} -> {payload['to']}[/yellow]")


def _describe(outcome: QuoteOutcome) -> str:
    match outcome:
        case QuoteSucceeded(result=r, attempts=n, from_cache=cached):
            origem = "cache" if cached else f"{n} tentativa(s)"
            texto = (
                f"[green]OK[/green] {r.plano_nome}: R$ {r.premio_mensal}/mês, "
                f"franquia R$ {r.franquia} ({origem})"
            )
            if r.primeiro_pagamento_pro_rata:
                pr = r.primeiro_pagamento_pro_rata
                texto += f"; 1º pagamento pro-rata R$ {pr.valor_primeiro_pagamento}"
            return texto
        case QuoteRefused(motivo=motivo):
            return f"[yellow]RECUSADA[/yellow] {motivo}"
        case QuoteInvalid(detalhe=detalhe):
            return f"[red]PEDIDO INVÁLIDO[/red] {detalhe}"
        case QuoteUnavailable(reason=reason, attempts=n, elapsed_ms=ms):
            return f"[red]INDISPONÍVEL[/red] {reason} após {n} tentativa(s) e {ms} ms"
    raise AssertionError(outcome)


def _outcome_label(outcome: QuoteOutcome) -> str:
    if isinstance(outcome, QuoteUnavailable):
        return f"unavailable:{outcome.reason}"
    return type(outcome).__name__.removeprefix("Quote").lower()


async def _run_quotes(
    settings: Settings,
    req: QuoteRequest,
    *,
    repeat: int,
    use_db: bool,
    use_cache: bool,
    quiet: bool,
) -> list[tuple[QuoteOutcome, float]]:
    async def run(events: EventStore, quotes: QuoteRepository) -> list[tuple[QuoteOutcome, float]]:
        async with httpx.AsyncClient(base_url=settings.quote_api_url) as http:
            client = QuoteClient(
                http=http,
                events=_PrintingEventStore(events, quiet=quiet),
                quotes=quotes,
                config=QuoteClientConfig.from_settings(settings),
                # TTL zero desliga o cache: toda repetição chama a API de verdade.
                cache=None if use_cache else QuoteCache(ttl_s=0),
            )
            results = []
            for i in range(repeat):
                if not quiet:
                    console.print(f"[bold]Cotação {i + 1}/{repeat}[/bold]")
                t0 = time.monotonic()
                outcome = await client.quote(req)
                elapsed = time.monotonic() - t0
                if not quiet:
                    console.print(f"  quote_id={outcome.quote_id}")
                    console.print("  " + _describe(outcome))
                results.append((outcome, elapsed))
            return results

    if not use_db:
        return await run(InMemoryEventStore(), InMemoryQuoteRepository())
    async with db.open_pool(settings) as pool:
        return await run(PostgresEventStore(pool), PostgresQuoteRepository(pool))


@app.command()
def quote(
    plano: Annotated[PlanId, typer.Option(help="Plano a cotar.")],
    idade: Annotated[int, typer.Option(help="Idade do condutor.")],
    ano: Annotated[int, typer.Option(help="Ano do veículo.")],
    cep: Annotated[str, typer.Option(help="CEP onde o carro dorme.")],
    inicio: Annotated[str, typer.Option(help="Início da vigência (ex.: amanhã, 15/11).")] = (
        "amanhã"
    ),
    repeat: Annotated[int, typer.Option(help="Quantas vezes cotar (no mesmo processo).")] = 1,
    as_json: Annotated[bool, typer.Option("--json", help="Saída em JSON.")] = False,
    no_db: Annotated[bool, typer.Option("--no-db", help="Não grava no Postgres.")] = False,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Chama a API em toda repetição.")
    ] = False,
) -> None:
    """Cota direto na API (sem LLM), mostrando cada tentativa. Útil para ver o cliente
    resiliente funcionando, inclusive com os modos de caos da quote-api."""
    settings = get_settings()
    # As tentativas já aparecem na saída; logs só para erro.
    configure_logging("ERROR", settings.log_format)

    cep_normalizado = normalize_cep(cep)
    if cep_normalizado is None:
        raise typer.BadParameter(f"CEP inválido: {cep!r}", param_hint="--cep")
    data_inicio = parse_data_inicio(inicio, today=date.today())
    if data_inicio is None:
        raise typer.BadParameter(f"Data não reconhecida: {inicio!r}", param_hint="--inicio")

    req = QuoteRequest(
        plano_id=plano, idade=idade, veiculo_ano=ano, cep=cep_normalizado, data_inicio=data_inicio
    )
    results = db.run_async(
        _run_quotes(
            settings,
            req,
            repeat=repeat,
            use_db=not no_db,
            use_cache=not no_cache,
            quiet=as_json,
        )
    )

    if as_json:
        print(json.dumps([asdict(o) for o, _ in results], default=str, ensure_ascii=False))
        return
    if repeat > 1:
        _print_summary(results)


def _print_summary(results: list[tuple[QuoteOutcome, float]]) -> None:
    counts = Counter(_outcome_label(o) for o, _ in results)
    durations = sorted(elapsed for _, elapsed in results)
    attempts = [o.attempts for o, _ in results]
    p95 = durations[max(0, round(0.95 * len(durations)) - 1)]
    console.print("\n[bold]Resumo[/bold]")
    for label, n in counts.most_common():
        console.print(f"  {label}: {n}")
    console.print(
        f"  tempo por cotação: p50 {statistics.median(durations):.2f} s, "
        f"p95 {p95:.2f} s, máx {durations[-1]:.2f} s"
    )
    console.print(f"  tentativas: média {statistics.mean(attempts):.2f}, máx {max(attempts)}")


# --- autoseguro data ----------------------------------------------------------------------

data_app = typer.Typer(help="Pipeline de dados do histórico de conversas.", no_args_is_help=True)
app.add_typer(data_app, name="data")


@data_app.command("silver")
def data_silver(
    dataset: Annotated[Path, typer.Option(help="Parquet original (Bronze).")] = DEFAULT_DATASET,
    out: Annotated[Path, typer.Option(help="Onde gravar a Silver.")] = DEFAULT_SILVER,
) -> None:
    """Gera a camada Silver: mensagens de lead ordenadas, mascaradas e com gabarito."""
    settings = get_settings()
    stats = build_silver(dataset, out, salt=settings.pseudonym_salt.get_secret_value())
    console.print(f"Silver gravada em {out}")
    console.print(f"  {stats.messages} mensagens de lead em {stats.conversations} conversas")
    for pii_type, count in stats.masked.items():
        console.print(f"  {pii_type.value} mascarados: {count}")

    leaks = find_leaks(out)
    if leaks:
        console.print(f"[red]{len(leaks)} mensagens ainda com dado pessoal[/red]")
        raise typer.Exit(code=1)
    console.print("[green]Nenhum dado pessoal no texto mascarado.[/green]")


# --- autoseguro eval ----------------------------------------------------------------------

eval_app = typer.Typer(
    help="Avaliações offline com o histórico de conversas.", no_args_is_help=True
)
app.add_typer(eval_app, name="eval")


async def _warm_up_and_evaluate(
    extractor: LLMExtractor, samples: list[Sample], concurrency: int
) -> list[SampleResult]:
    # No mesmo event loop: o cliente HTTP do modelo fica preso ao loop em que foi usado.
    # Uma chamada fora da medição antes, porque a primeira carrega o modelo (no Ollama, na GPU).
    await warm_up(extractor)
    return await evaluate(extractor, samples, concurrency=concurrency)


@eval_app.command("extraction")
def eval_extraction(
    model: Annotated[
        list[str], typer.Option(help="Modelo(s) a comparar, ex.: Qwen/Qwen3.5-27B:deepinfra.")
    ],
    n: Annotated[int, typer.Option(help="Conversas sorteadas da Silver.")] = 30,
    seed: Annotated[int, typer.Option(help="Semente do sorteio.")] = 42,
    concurrency: Annotated[int, typer.Option(help="Chamadas simultâneas.")] = 4,
    silver: Annotated[Path, typer.Option(help="Camada Silver.")] = DEFAULT_SILVER,
    out_dir: Annotated[Path, typer.Option(help="Onde gravar os resultados por conversa.")] = (
        DEFAULT_SILVER.parents[1] / "eval"
    ),
    report: Annotated[
        Path | None, typer.Option(help="Arquivo Markdown para a tabela comparativa.")
    ] = None,
) -> None:
    """Mede acurácia, erros, latência e custo de cada modelo na extração de dados."""
    settings = get_settings()
    if not silver.exists():
        raise typer.BadParameter(f"{silver} não existe: rode `autoseguro data silver`.")
    samples = load_samples(silver, n, seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    for model_id in model:
        console.print(f"[bold]{model_id}[/bold]: {len(samples)} conversas…")
        extractor = LLMExtractor(get_chat_model(settings, model=model_id))
        results = db.run_async(_warm_up_and_evaluate(extractor, samples, concurrency))
        summary = summarize(model_id, results)
        summaries.append(summary)
        slug = model_id.replace("/", "__").replace(":", "--")
        (out_dir / f"extraction_{slug}.json").write_text(
            json.dumps(
                {"summary": summary, "results": results_to_json(results)},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        errors = [r.error for r in results if r.error]
        if errors:
            console.print(f"  [yellow]{len(errors)} erro(s); o primeiro: {errors[0]}[/yellow]")

    table = render_markdown(summaries)
    console.print(table)
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(table, encoding="utf-8")
        console.print(f"Tabela gravada em {report}")
