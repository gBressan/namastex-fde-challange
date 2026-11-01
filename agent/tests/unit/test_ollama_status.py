from __future__ import annotations

import httpx
import pytest
import respx

from autoseguro.config import Settings
from autoseguro.llm.ollama import ollama_status

SETTINGS = Settings(ollama_base_url="http://ollama.test:11434/v1", _env_file=None)
TAGS = {"models": [{"name": "qwen3.5:4b"}, {"name": "outro:1b"}]}


@pytest.mark.parametrize(
    ("ps", "loaded", "gpu"),
    [
        ({"models": []}, False, None),
        ({"models": [{"name": "qwen3.5:4b", "size": 3100, "size_vram": 3100}]}, True, 1.0),
        ({"models": [{"name": "qwen3.5:4b", "size": 4000, "size_vram": 1000}]}, True, 0.25),
        ({"models": [{"name": "qwen3.5:4b", "size": 3100, "size_vram": 0}]}, True, 0.0),
    ],
    ids=["nao-carregado", "100-gpu", "parte-cpu", "so-cpu"],
)
def test_status_mostra_quanto_do_modelo_esta_na_gpu(
    ps: dict[str, object], loaded: bool, gpu: float | None
) -> None:
    with respx.mock(base_url="http://ollama.test:11434") as api:
        api.get("/api/tags").mock(return_value=httpx.Response(200, json=TAGS))
        api.get("/api/ps").mock(return_value=httpx.Response(200, json=ps))
        status = ollama_status(SETTINGS)

    assert status.downloaded
    assert status.loaded is loaded
    assert status.gpu_fraction == gpu


def test_modelo_nao_baixado() -> None:
    with respx.mock(base_url="http://ollama.test:11434") as api:
        api.get("/api/tags").mock(return_value=httpx.Response(200, json={"models": []}))
        api.get("/api/ps").mock(return_value=httpx.Response(200, json={"models": []}))
        assert not ollama_status(SETTINGS).downloaded
