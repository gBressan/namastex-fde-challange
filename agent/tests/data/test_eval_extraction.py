"""Amostragem e métricas da comparação de modelos (sem chamar LLM)."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoseguro.data.silver import DEFAULT_DATASET, build_silver
from autoseguro.eval.extraction import (
    SampleResult,
    evaluate,
    load_samples,
    render_markdown,
    summarize,
)
from autoseguro.llm.extractor import ExtractionDetails, ExtractionInput
from autoseguro.llm.schemas import Extraction, Intent


@pytest.fixture(scope="module")
def silver(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("silver") / "lead_messages.parquet"
    build_silver(DEFAULT_DATASET, out, salt="teste")
    return out


@pytest.mark.data
def test_amostra_e_reprodutivel_e_vai_ate_a_mensagem_com_os_dados(silver: Path) -> None:
    first = load_samples(silver, 20, seed=7)
    again = load_samples(silver, 20, seed=7)
    other = load_samples(silver, 20, seed=8)

    assert first == again
    assert first != other
    assert len(first) == 20
    for sample in first:
        assert any("[CPF]" in m for m in sample.lead_messages)
        assert not any("fechado" in m for m in sample.lead_messages)  # parou antes do desfecho
        assert sample.gt_cep_prefixo in " ".join(sample.lead_messages)


class PerfectExtractor:
    """Lê os dados direto das mensagens (para testar a contagem, não o LLM)."""

    def __init__(self, samples: dict[tuple[str, ...], tuple[int, int, str]]) -> None:
        self._by_messages = samples

    async def extract_with_details(self, data: ExtractionInput) -> ExtractionDetails:
        idade, ano, cep = self._by_messages[tuple(data.lead_messages)]
        extraction = Extraction(
            intent=Intent.INFORMAR_DADOS, idade=idade, veiculo_ano=ano, cep=cep, confidence=1
        )
        return ExtractionDetails(extraction, 1, 0.5, 1000, 50)


@pytest.mark.data
async def test_evaluate_conta_acertos(silver: Path) -> None:
    samples = load_samples(silver, 5, seed=1)
    truth = {
        tuple(s.lead_messages): (s.gt_idade, s.gt_veiculo_ano, f"{s.gt_cep_prefixo}000-000")
        for s in samples
    }
    results = await evaluate(PerfectExtractor(truth), samples, concurrency=2)
    assert all(r.ok_idade and r.ok_veiculo_ano and r.ok_cep for r in results)


def test_summarize_e_render() -> None:
    results = [
        SampleResult("c1", True, True, True, 1, 0.4, 1000, 50),
        SampleResult("c2", False, True, True, 2, 0.8, 2000, 100),
        SampleResult("c3", False, False, False, 0, 0.0, 0, 0, error="TimeoutError"),
        SampleResult("c4", True, True, False, 1, 0.6, 1000, 50),
    ]
    s = summarize("Qwen/Qwen3.5-9B:deepinfra", results)

    assert s["acc_idade"] == 0.5
    assert s["acc_veiculo_ano"] == 0.75
    assert s["acc_cep"] == 0.5
    assert s["erros"] == 1
    assert s["retry_formato"] == 1
    assert s["latencia_p50_s"] == 0.6
    # 4000 tokens de entrada a US$ 0,10/M + 200 de saída a US$ 0,15/M
    assert s["custo_usd"] == pytest.approx((4000 * 0.10 + 200 * 0.15) / 1e6)
    assert "| `Qwen/Qwen3.5-9B:deepinfra` | 4 | 50.0% | 75.0% | 50.0% | 1 | 1 |" in (
        render_markdown([s])
    )
