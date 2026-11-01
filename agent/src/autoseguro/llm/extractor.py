"""Extração tipada: transforma as mensagens do lead em um `Extraction`.

`LLMExtractor` usa o modelo com function calling; `ScriptedExtractor` devolve respostas
prontas (testes e cenários golden), sem rede.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from autoseguro.llm.schemas import Extraction


def load_prompt(name: str) -> str:
    return (resources.files("autoseguro.llm") / "prompts" / f"{name}.md").read_text("utf-8")


class ExtractionError(RuntimeError):
    """O modelo não devolveu uma extração válida (nem depois de tentar de novo)."""


@dataclass(frozen=True)
class ExtractionInput:
    """O que o extrator recebe: mensagens do lead **já mascaradas**, da mais antiga para a
    mais nova, e a última pergunta do assistente (ajuda a entender respostas curtas)."""

    lead_messages: Sequence[str]
    last_question: str | None = None


@dataclass(frozen=True)
class ExtractionDetails:
    extraction: Extraction
    attempts: int
    latency_s: float
    input_tokens: int
    output_tokens: int


class Extractor(Protocol):
    async def extract(self, data: ExtractionInput) -> Extraction: ...


def render_input(data: ExtractionInput) -> str:
    lines = []
    if data.last_question:
        lines.append(f"Última pergunta do assistente: {data.last_question}")
    lines.append("Mensagens do lead (da mais antiga para a mais nova):")
    lines.extend(f'- "{message}"' for message in data.lead_messages)
    return "\n".join(lines)


class LLMExtractor:
    def __init__(self, model: BaseChatModel, *, max_attempts: int = 2) -> None:
        self._chain = model.with_structured_output(
            Extraction, method="function_calling", include_raw=True
        )
        self._system = load_prompt("extraction")
        self._max_attempts = max_attempts

    async def extract(self, data: ExtractionInput) -> Extraction:
        return (await self.extract_with_details(data)).extraction

    async def extract_with_details(self, data: ExtractionInput) -> ExtractionDetails:
        messages: list[BaseMessage] = [
            SystemMessage(self._system),
            HumanMessage(render_input(data)),
        ]
        t0 = time.monotonic()
        input_tokens = output_tokens = 0
        last_error = ""
        for attempt in range(1, self._max_attempts + 1):
            result: dict[str, Any] = await self._chain.ainvoke(messages)  # type: ignore[assignment]
            raw = result.get("raw")
            usage = getattr(raw, "usage_metadata", None) or {}
            input_tokens += int(usage.get("input_tokens", 0))
            output_tokens += int(usage.get("output_tokens", 0))

            parsed = result.get("parsed")
            if isinstance(parsed, Extraction):
                return ExtractionDetails(
                    parsed, attempt, time.monotonic() - t0, input_tokens, output_tokens
                )

            # Saída inválida: devolve o erro ao modelo e pede de novo, uma vez.
            last_error = str(result.get("parsing_error") or "o modelo não chamou a função")
            if isinstance(raw, AIMessage):
                messages.append(raw)
            messages.append(
                HumanMessage(
                    f"Sua resposta não seguiu o formato ({last_error[:300]}). "
                    "Chame a função de novo com os campos corretos."
                )
            )
        raise ExtractionError(last_error)


class ScriptedExtractor:
    """Devolve as extrações dadas, na ordem. Para testes e cenários golden."""

    def __init__(self, extractions: Iterable[Extraction | dict[str, Any]]) -> None:
        self._queue: deque[Extraction] = deque(
            e if isinstance(e, BaseModel) else Extraction.model_validate(e) for e in extractions
        )
        self.received: list[ExtractionInput] = []

    async def extract(self, data: ExtractionInput) -> Extraction:
        self.received.append(data)
        if not self._queue:
            raise ExtractionError("ScriptedExtractor sem respostas: o cenário pediu uma a mais")
        return self._queue.popleft()
