"""Configuração da aplicação, lida de variáveis de ambiente (e de um `.env`, se existir)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # `.env` na pasta atual ou na raiz do repo (quando rodando de dentro de agent/).
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    quote_api_url: str = "http://localhost:8000"
    database_url: str = "postgresql://autoseguro:autoseguro@localhost:5432/autoseguro"

    # `ollama`: modelo local (padrão, sem custo). `openai_compatible`: qualquer API compatível
    # com a da OpenAI, como o router do Hugging Face (LLM_BASE_URL + token).
    llm_provider: Literal["ollama", "openai_compatible"] = "ollama"
    llm_model: str = "qwen3.5:4b"
    ollama_base_url: str = "http://localhost:11434/v1"
    llm_base_url: str = "https://router.huggingface.co/v1"
    llm_api_key: SecretStr | None = None
    llm_timeout_s: float = 20.0
    llm_disable_thinking: bool = True

    quote_timeout_s: float = 2.5
    quote_max_attempts: int = 4
    quote_deadline_s: float = 12.0

    # Sal do pseudônimo dos remetentes na camada Silver. O dataset é sintético, então um valor
    # fixo basta para reprodutibilidade; com dados reais, viria de um cofre de segredos.
    pseudonym_salt: SecretStr = SecretStr("autoseguro-silver-v1")

    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    @property
    def sqlalchemy_url(self) -> str:
        """URL no formato do SQLAlchemy/Alembic (`postgresql+psycopg://`).

        O psycopg usa `postgresql://`; o SQLAlchemy precisa do driver explícito.
        """
        scheme, sep, rest = self.database_url.partition("://")
        if scheme in ("postgres", "postgresql"):
            return f"postgresql+psycopg{sep}{rest}"
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
