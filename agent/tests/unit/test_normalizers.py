from __future__ import annotations

from datetime import date

import pytest

from autoseguro.domain.normalizers import (
    DataInicioProblema,
    check_data_inicio,
    format_cep,
    mask_cep_for_log,
    normalize_ano_veiculo,
    normalize_cep,
    normalize_idade,
    parse_data_inicio,
)

TODAY = date(2026, 10, 29)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("01310-100", "01310100"),
        ("01310100", "01310100"),
        ("CEP 21310-100", "21310100"),
        ("cep 26703-384, cpf 389.083.863-43", "26703384"),
        ("21.310-100", "21310100"),
        ("(21) 310-100", "21310100"),
        ("  07123-456  ", "07123456"),
        ("0131-0100", "01310100"),
        ("01310-10", None),
        ("013101000", None),
        ("sem cep", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_cep(text: str | None, expected: str | None) -> None:
    assert normalize_cep(text) == expected


def test_format_and_mask_cep() -> None:
    assert format_cep("01310100") == "01310-100"
    assert mask_cep_for_log("21310100") == "21***-***"
    with pytest.raises(ValueError):
        format_cep("0131010")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (35, 35),
        ("35", 35),
        ("tenho 35 anos", 35),
        ("Tenho 35 anos, cep 01310-100", 35),
        ("cep 01310-100, tenho 78 anos", 78),
        ("17", 17),
        (0, 0),
        (120, 120),
        (121, None),
        (-1, None),
        ("nao sei", None),
        (None, None),
    ],
)
def test_normalize_idade(value: int | str | None, expected: int | None) -> None:
    assert normalize_idade(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (2022, 2022),
        ("2022", 2022),
        ("Toyota Corolla 2008", 2008),
        ("e um Sandero 2022", 2022),
        ("Onix, ano 2027", 2027),  # ano-modelo: até o ano atual + 1
        (2028, None),
        ("ano 22", 2022),
        ("ano 05", 2005),
        ("ano 99", 1999),
        (99, 1999),
        ("ano 30", None),  # 1930: antes de 1950
        (1949, None),
        (1950, 1950),
        ("nao lembro", None),
        (None, None),
    ],
)
def test_normalize_ano_veiculo(value: int | str | None, expected: int | None) -> None:
    assert normalize_ano_veiculo(value, today=TODAY) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hoje", date(2026, 10, 29)),
        ("amanhã", date(2026, 10, 30)),
        ("amanha", date(2026, 10, 30)),
        ("depois de amanhã", date(2026, 10, 31)),
        ("mês que vem", date(2026, 11, 1)),
        ("2026-11-15", date(2026, 11, 15)),
        ("15/11/2026", date(2026, 11, 15)),
        ("15/11/26", date(2026, 11, 15)),
        ("15/11", date(2026, 11, 15)),
        ("10/10", date(2027, 10, 10)),  # já passou este ano: próxima ocorrência
        ("29/02", date(2028, 2, 29)),  # próximo ano bissexto
        ("dia 15", date(2026, 11, 15)),
        ("dia 30", date(2026, 10, 30)),
        ("dia 31", date(2026, 10, 31)),
        ("dia 1º", date(2026, 11, 1)),
        ("dia primeiro", date(2026, 11, 1)),
        ("1º de dezembro", date(2026, 12, 1)),
        ("15 de março", date(2027, 3, 15)),
        ("31/02/2027", None),
        ("quando der", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_data_inicio(text: str | None, expected: date | None) -> None:
    assert parse_data_inicio(text, today=TODAY) == expected


def test_dia_31_pula_meses_de_30_dias() -> None:
    assert parse_data_inicio("dia 31", today=date(2026, 11, 2)) == date(2026, 12, 31)


def test_virada_do_ano() -> None:
    assert parse_data_inicio("mês que vem", today=date(2026, 12, 10)) == date(2027, 1, 1)
    assert parse_data_inicio("dia 5", today=date(2026, 12, 10)) == date(2027, 1, 5)


@pytest.mark.parametrize(
    ("inicio", "expected"),
    [
        (date(2026, 10, 29), None),
        (date(2027, 1, 27), None),  # exatamente 90 dias
        (date(2027, 1, 28), DataInicioProblema.MUITO_DISTANTE),
        (date(2026, 10, 28), DataInicioProblema.PASSADA),
        (date(2028, 2, 29), DataInicioProblema.MUITO_DISTANTE),
    ],
)
def test_check_data_inicio(inicio: date, expected: DataInicioProblema | None) -> None:
    assert check_data_inicio(inicio, today=TODAY) == expected
