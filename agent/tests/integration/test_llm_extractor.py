"""LLMExtractor contra um endpoint compatível com OpenAI simulado: o mesmo formato que o
router do Hugging Face devolve.

O SDK da OpenAI usa o `httpx2` (não o `httpx`), então o respx não o intercepta: o endpoint
falso entra como um `httpx2.MockTransport` no cliente HTTP do modelo.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest

from autoseguro.config import Settings
from autoseguro.llm.extractor import (
    ExtractionError,
    ExtractionInput,
    LLMExtractor,
    ScriptedExtractor,
    render_input,
)
from autoseguro.llm.factory import MissingLLMCredentialsError, get_chat_model, resolve_api_key
from autoseguro.llm.schemas import Intent

BASE_URL = "http://llm.test/v1"
INPUT = ExtractionInput(
    lead_messages=["e um Onix Plus 2022", "Cpf [CPF], tenho 41 anos, cep 01335-846"],
    last_question="Qual o modelo e ano do veículo?",
)
VALID_ARGS = {
    "intent": "informar_dados",
    "veiculo_descricao": "Onix Plus",
    "veiculo_ano": 2022,
    "idade": 41,
    "cep": "01335-846",
    "confidence": 0.95,
}


def completion(arguments: dict[str, Any] | str, *, prompt_tokens: int = 900) -> httpx2.Response:
    args = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return httpx2.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 0,
            "model": "Qwen/Qwen3.5-27B",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "Extraction", "arguments": args},
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 40, "total_tokens": 0},
        },
    )


class FakeLLM:
    """Endpoint falso: devolve as respostas da fila e guarda as requisições."""

    def __init__(self) -> None:
        self.responses: list[httpx2.Response] = []
        self.requests: list[httpx2.Request] = []

    def reply(self, *responses: httpx2.Response) -> None:
        self.responses.extend(responses)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        assert request.url.path == "/v1/chat/completions"
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    def body(self, index: int = -1) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.requests[index].content)
        return data

    def extractor(self, settings: Settings) -> LLMExtractor:
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler))
        return LLMExtractor(get_chat_model(settings, http_async_client=client))


@pytest.fixture
def settings() -> Settings:
    return Settings(llm_base_url=BASE_URL, llm_api_key="hf_teste", _env_file=None)


@pytest.fixture
def api() -> Iterator[FakeLLM]:
    yield FakeLLM()


async def test_extrai_dados_tipados(settings: Settings, api: FakeLLM) -> None:
    api.reply(completion(VALID_ARGS))

    details = await api.extractor(settings).extract_with_details(INPUT)

    e = details.extraction
    assert (e.intent, e.idade, e.veiculo_ano, e.cep) == (
        Intent.INFORMAR_DADOS,
        41,
        2022,
        "01335-846",
    )
    assert details.attempts == 1
    assert (details.input_tokens, details.output_tokens) == (900, 40)

    body = api.body()
    assert body["temperature"] == 0
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["tools"][0]["function"]["name"] == "Extraction"
    assert "Onix Plus 2022" in body["messages"][1]["content"]
    assert api.requests[-1].headers["authorization"] == "Bearer hf_teste"


async def test_saida_invalida_e_corrigida_na_segunda_tentativa(
    settings: Settings, api: FakeLLM
) -> None:
    sem_confianca = {k: v for k, v in VALID_ARGS.items() if k != "confidence"}
    api.reply(completion(sem_confianca), completion(VALID_ARGS))

    details = await api.extractor(settings).extract_with_details(INPUT)

    assert details.attempts == 2
    assert details.input_tokens == 1800  # soma as duas chamadas
    segunda = api.body()["messages"]
    assert "não seguiu o formato" in segunda[-1]["content"]


async def test_saida_sempre_invalida_vira_extraction_error(
    settings: Settings, api: FakeLLM
) -> None:
    api.reply(completion("{nao e json"))

    with pytest.raises(ExtractionError):
        await api.extractor(settings).extract(INPUT)
    assert len(api.requests) == 2  # uma tentativa de correção, não mais


async def test_cliente_nao_repete_chamada_por_conta_propria(
    settings: Settings, api: FakeLLM
) -> None:
    api.reply(httpx2.Response(503, json={}))

    with pytest.raises(Exception):  # noqa: B017 — o tipo vem do SDK da OpenAI
        await api.extractor(settings).extract(INPUT)
    assert len(api.requests) == 1  # o retry é nosso, numa camada só


def test_render_input_inclui_pergunta_e_mensagens_em_ordem() -> None:
    text = render_input(INPUT)
    assert text.index("Qual o modelo") < text.index("Onix Plus") < text.index("41 anos")


async def test_scripted_extractor_devolve_na_ordem() -> None:
    extractor = ScriptedExtractor([{**VALID_ARGS}, {"intent": "aceitar_proposta", "confidence": 1}])
    assert (await extractor.extract(INPUT)).idade == 41
    assert (await extractor.extract(INPUT)).intent is Intent.ACEITAR_PROPOSTA
    with pytest.raises(ExtractionError):
        await extractor.extract(INPUT)
    assert len(extractor.received) == 3


def test_token_vem_de_llm_api_key_depois_hf_token_depois_login_do_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("hf_do_cli\n", encoding="utf-8")
    monkeypatch.setenv("HF_TOKEN_PATH", str(token_file))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    sem_chave = Settings(_env_file=None)

    key = resolve_api_key(sem_chave)
    assert key is not None and key.get_secret_value() == "hf_do_cli"

    monkeypatch.setenv("HF_TOKEN", "hf_da_env")
    key = resolve_api_key(sem_chave)
    assert key is not None and key.get_secret_value() == "hf_da_env"

    com_chave = Settings(llm_api_key="hf_do_env_file", _env_file=None)
    key = resolve_api_key(com_chave)
    assert key is not None and key.get_secret_value() == "hf_do_env_file"


def test_sem_token_nenhum_falha_com_mensagem_clara(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("HF_TOKEN_PATH", str(tmp_path / "nao_existe"))
    with pytest.raises(MissingLLMCredentialsError, match="hf auth login"):
        get_chat_model(Settings(_env_file=None))
