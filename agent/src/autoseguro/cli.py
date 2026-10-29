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
from typing import Annotated, Any
from uuid import UUID

import httpx
import psycopg
import typer
from rich.console import Console

from autoseguro import db
from autoseguro.config import Settings, get_settings
from autoseguro.domain.models import PlanId, QuoteRequest
from autoseguro.domain.normalizers import normalize_cep, parse_data_inicio
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

    llm_key = "configurada" if settings.llm_api_key else "[yellow]não configurada[/yellow]"
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
