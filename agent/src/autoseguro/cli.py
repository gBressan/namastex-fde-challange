"""Linha de comando `autoseguro`."""

from __future__ import annotations

import httpx
import psycopg
import typer
from rich.console import Console

from autoseguro import db
from autoseguro.config import get_settings
from autoseguro.log import configure_logging

app = typer.Typer(help="Agente de cotação AutoSeguro.", no_args_is_help=True)
db_app = typer.Typer(help="Banco de dados (Postgres).", no_args_is_help=True)
app.add_typer(db_app, name="db")

console = Console()


@app.callback()
def _setup() -> None:
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
