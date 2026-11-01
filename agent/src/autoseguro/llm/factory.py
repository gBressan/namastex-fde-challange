"""Criação do modelo de chat (qualquer endpoint compatível com a API da OpenAI).

O padrão é o router do Hugging Face Inference Providers. O token vem, nesta ordem, de
`LLM_API_KEY`, de `HF_TOKEN` ou do login do `hf` CLI (`hf auth login`).
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
    api_key = resolve_api_key(settings)
    if api_key is None:
        raise MissingLLMCredentialsError(
            "Sem token do LLM: defina LLM_API_KEY ou HF_TOKEN, ou rode `hf auth login`."
        )
    extra_body = (
        # Qwen pensa antes de responder por padrão. Para extrair dados, isso só custa
        # latência e tokens; desligamos (provedores que não reconhecem ignoram o campo).
        {"chat_template_kwargs": {"enable_thinking": False}}
        if settings.llm_disable_thinking
        else None
    )
    return ChatOpenAI(
        model=model or settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=api_key,
        temperature=0,
        timeout=settings.llm_timeout_s,
        # O retry é nosso (numa camada só): o cliente não repete por conta própria.
        max_retries=0,
        extra_body=extra_body,
        http_async_client=http_async_client,
    )
