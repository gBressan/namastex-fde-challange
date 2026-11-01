"""Criação do modelo de chat.

Os dois provedores falam a mesma API (a da OpenAI), então o resto do código não sabe qual
está em uso:

- `ollama` (padrão): Qwen rodando localmente no container `ollama`, sem custo e sem token;
- `openai_compatible`: qualquer endpoint compatível, como o router do Hugging Face. O token
  vem, nesta ordem, de `LLM_API_KEY`, de `HF_TOKEN` ou do login do `hf` CLI.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from autoseguro.config import Settings


class MissingLLMCredentialsError(RuntimeError):
    pass


def resolve_api_key(settings: Settings) -> SecretStr | None:
    if settings.llm_api_key:
        return settings.llm_api_key
    if token := os.getenv("HF_TOKEN"):
        return SecretStr(token)
    # Mesmo lugar onde o `hf auth login` grava o token.
    hf_home = Path(os.getenv("HF_HOME", Path.home() / ".cache" / "huggingface"))
    token_file = Path(os.getenv("HF_TOKEN_PATH", hf_home / "token"))
    if token_file.is_file() and (token := token_file.read_text(encoding="utf-8").strip()):
        return SecretStr(token)
    return None


def get_chat_model(
    settings: Settings, *, model: str | None = None, http_async_client: Any = None
) -> ChatOpenAI:
    """`http_async_client` permite injetar um cliente com transporte simulado nos testes."""
    # Qwen pensa antes de responder por padrão. Para extrair dados isso só custa latência,
    # então desligamos; cada provedor tem o seu jeito de pedir isso.
    thinking: dict[str, Any] = {}

    if settings.llm_provider == "ollama":
        base_url = settings.ollama_base_url
        api_key = SecretStr("ollama")  # o Ollama não confere; o SDK só exige um valor
        if settings.llm_disable_thinking:
            thinking = {"reasoning_effort": "none"}
    else:
        base_url = settings.llm_base_url
        resolved = resolve_api_key(settings)
        if resolved is None:
            raise MissingLLMCredentialsError(
                "Sem token do LLM: defina LLM_API_KEY ou HF_TOKEN, ou rode `hf auth login`."
            )
        api_key = resolved
        if settings.llm_disable_thinking:
            thinking = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}

    return ChatOpenAI(
        model=model or settings.llm_model,
        base_url=base_url,
        api_key=api_key,
        temperature=0,
        timeout=settings.llm_timeout_s,
        # O retry é nosso (numa camada só): o cliente não repete por conta própria.
        max_retries=0,
        http_async_client=http_async_client,
        **thinking,
    )
