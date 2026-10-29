"""Normalizadores puros: transformam o que o lead escreveu em valores que a API aceita.

Recebem `today` por parâmetro sempre que a data atual importa, para os testes não dependerem
do relógio.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date, timedelta
from enum import StrEnum

MAX_IDADE = 120
MIN_ANO_VEICULO = 1950
MAX_DIAS_INICIO = 90

_CEP_PATTERN = re.compile(r"(?<!\d)(\d{2})\.?(\d{3})-?(\d{3})(?!\d)")
_ANO_4_DIGITOS = re.compile(r"(?<!\d)(19|20)(\d{2})(?!\d)")
_ANO_2_DIGITOS = re.compile(r"(?<!\d)(\d{2})(?!\d)")
_NUMERO = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")
_IDADE_ANOS = re.compile(r"(?<!\d)(\d{1,3})\s*anos")
_DATA_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_DATA_BR = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?(?!\d)")
_DIA = re.compile(r"\bdia\s+(\d{1,2}|primeiro)\b")
_DIA_DE_MES = re.compile(r"(?<!\d)(\d{1,2}|primeiro)\s+de\s+([a-z]+)")

_MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}  # fmt: skip


def _fold(text: str) -> str:
    """Minúsculas, sem acentos e com ordinais (1º, 1ª) virando dígitos simples."""
    text = text.replace("º", "").replace("ª", "").replace("°", "")
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


# --- CEP ---------------------------------------------------------------------------------


def normalize_cep(text: str | None) -> str | None:
    """Devolve o CEP com 8 dígitos (ex.: "01310100") ou `None` se não houver um CEP válido.

    Aceita "01310-100", "01.310-100", "CEP 01310100", "(21) 310-100".
    """
    if not text:
        return None
    match = _CEP_PATTERN.search(text)
    if match:
        return "".join(match.groups())
    digits = re.sub(r"\D", "", text)
    return digits if len(digits) == 8 else None


def format_cep(cep: str) -> str:
    """ "01310100" -> "01310-100" (formato enviado à API)."""
    if not re.fullmatch(r"\d{8}", cep):
        raise ValueError(f"CEP deve ter 8 dígitos: {cep!r}")
    return f"{cep[:5]}-{cep[5:]}"


def mask_cep_for_log(cep: str) -> str:
    """ "21310100" -> "21***-***": nos logs só aparece o prefixo, que define a região."""
    return f"{cep[:2]}***-***"


# --- Idade --------------------------------------------------------------------------------


def normalize_idade(value: int | str | None) -> int | None:
    """Idade em anos entre 0 e 120, ou `None`.

    Não aplica a regra de aceitação (18-75): isso é decisão da API.
    """
    if value is None:
        return None
    if isinstance(value, int):
        idade = value
    else:
        match = _IDADE_ANOS.search(value) or _NUMERO.search(value)
        if not match:
            return None
        idade = int(match.group(1))
    return idade if 0 <= idade <= MAX_IDADE else None


# --- Ano do veículo -----------------------------------------------------------------------


def normalize_ano_veiculo(value: int | str | None, *, today: date) -> int | None:
    """Ano do veículo com 4 dígitos, entre 1950 e o ano atual + 1 (ano-modelo), ou `None`.

    Dois dígitos ("ano 05") viram 20xx quando cabem até o ano atual + 1; senão 19xx.
    """
    if value is None:
        return None
    max_ano = today.year + 1
    if isinstance(value, int):
        ano = value if value >= 100 else _expand_two_digit_year(value, max_ano)
    else:
        match4 = _ANO_4_DIGITOS.search(value)
        if match4:
            ano = int(match4.group(1) + match4.group(2))
        else:
            match2 = _ANO_2_DIGITOS.search(value)
            if not match2:
                return None
            ano = _expand_two_digit_year(int(match2.group(1)), max_ano)
    return ano if MIN_ANO_VEICULO <= ano <= max_ano else None


def _expand_two_digit_year(yy: int, max_ano: int) -> int:
    candidate = 2000 + yy
    return candidate if candidate <= max_ano else 1900 + yy


# --- Data de início da vigência -----------------------------------------------------------


class DataInicioProblema(StrEnum):
    PASSADA = "passada"
    MUITO_DISTANTE = "muito_distante"


def parse_data_inicio(text: str | None, *, today: date) -> date | None:
    """Interpreta a data de início que o lead escreveu. Devolve `None` se não entender.

    Aceita "hoje", "amanhã", "depois de amanhã", "mês que vem" (dia 1º do próximo mês),
    "2026-11-15", "15/11/2026", "15/11/26", "15/11", "dia 15", "dia 1º", "15 de novembro".
    Datas sem ano ("15/11", "dia 15") são a próxima ocorrência a partir de hoje.

    Não valida se a data é aceitável: use `check_data_inicio` para isso.
    """
    if not text:
        return None
    t = _fold(text).strip()

    if "depois de amanha" in t:
        return today + timedelta(days=2)
    if "amanha" in t:
        return today + timedelta(days=1)
    if "hoje" in t or "agora" in t or "imediat" in t:
        return today
    if "mes que vem" in t or "proximo mes" in t:
        return _first_day_next_month(today)

    if m := _DATA_ISO.search(t):
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    if m := _DATA_BR.search(t):
        day, month = int(m.group(1)), int(m.group(2))
        if m.group(3):
            year = int(m.group(3))
            year = year + 2000 if year < 100 else year
            return _safe_date(year, month, day)
        return _next_occurrence(day, month, today)

    if m := _DIA_DE_MES.search(t):
        month_num = _MESES.get(m.group(2))
        if month_num:
            return _next_occurrence(_parse_day(m.group(1)), month_num, today)

    if m := _DIA.search(t):
        return _next_day_of_month(_parse_day(m.group(1)), today)

    return None


def check_data_inicio(inicio: date, *, today: date) -> DataInicioProblema | None:
    """`None` se a data é aceitável; senão o problema, para o agente explicar ao lead."""
    if inicio < today:
        return DataInicioProblema.PASSADA
    if inicio > today + timedelta(days=MAX_DIAS_INICIO):
        return DataInicioProblema.MUITO_DISTANTE
    return None


def _parse_day(token: str) -> int:
    return 1 if token == "primeiro" else int(token)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _first_day_next_month(today: date) -> date:
    return date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)


def _next_occurrence(day: int, month: int, today: date) -> date | None:
    """Próxima data com esse dia/mês a partir de hoje (inclusive). Ex.: 29/02 pula para o
    próximo ano bissexto."""
    for year in range(today.year, today.year + 5):
        candidate = _safe_date(year, month, day)
        if candidate and candidate >= today:
            return candidate
    return None


def _next_day_of_month(day: int, today: date) -> date | None:
    """Próximo "dia N" a partir de hoje: este mês se ainda não passou, senão o seguinte
    que tenha esse dia (dia 31 pula meses de 30 dias)."""
    if not 1 <= day <= 31:
        return None
    year, month = today.year, today.month
    for _ in range(12):
        if day <= calendar.monthrange(year, month)[1]:
            candidate = date(year, month, day)
            if candidate >= today:
                return candidate
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return None
