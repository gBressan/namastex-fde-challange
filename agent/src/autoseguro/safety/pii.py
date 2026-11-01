"""Mascaramento de dados pessoais (CPF, telefone, e-mail, placa).

A cotação precisa só de idade, ano do veículo, CEP, plano e data (princípio da necessidade,
LGPD art. 6º, III). Todo o resto é mascarado **antes** de gravar, logar ou mandar para o LLM;
o texto original não é guardado em lugar nenhum.

O CEP não é mascarado aqui: é dado de negócio, usado na cotação. Nos logs ele aparece só com
o prefixo (`autoseguro.domain.normalizers.mask_cep_for_log`).

As regras ficam em regex + validação (dígito verificador do CPF), que é auditável e cobre os
formatos do dataset. Um NER (ex.: Presidio) fica como evolução.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping, MutableMapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PIIType(StrEnum):
    CPF = "CPF"
    TELEFONE = "TELEFONE"
    EMAIL = "EMAIL"
    PLACA = "PLACA"


@dataclass(frozen=True)
class PIIEntity:
    """Onde estava um dado pessoal. Nunca guarda o valor."""

    type: PIIType
    start: int
    end: int
    valid: bool | None = None  # para CPF: se o dígito verificador confere


@dataclass(frozen=True)
class RedactionResult:
    text: str
    entities: tuple[PIIEntity, ...]

    def types(self) -> set[PIIType]:
        return {e.type for e in self.entities}


# Fronteiras: nem letra, nem dígito, nem "_" colados. Evita pegar pedaços de UUIDs e hashes.
_B = r"(?<![\w])"
_E = r"(?![\w])"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE)
# CPF formatado (000.000.000-00 ou com espaços): é CPF mesmo com dígito errado.
_CPF_FORMATTED = re.compile(_B + r"\d{3}[.\s]\d{3}[.\s]\d{3}[-.\s]\d{2}" + _E)
# 11 dígitos seguidos: pode ser CPF ou celular com DDD. Decide pelo contexto e pelo checksum.
_ELEVEN_DIGITS = re.compile(_B + r"\d{11}" + _E)
_TELEFONE = re.compile(
    _B + r"(?:\+?55[\s-]?)?\(?\d{2}\)?[\s-]?9?\d{4}[\s-]?\d{4}" + _E
    + r"|" + _B + r"9\d{4}-\d{4}" + _E  # celular sem DDD: 98646-8699
)  # fmt: skip
# Placa Mercosul (ABC1D23) e antiga (ABC1234 / ABC-1234).
_PLACA = re.compile(_B + r"([A-Za-z]{3})-?\d[A-Za-z\d]\d{2}" + _E)
# Palavras de 3 letras que, coladas a um número, não são placa ("ano2022", "cep0131").
_PLACA_STOPWORDS = {"ano", "mod", "cep", "cpf", "tel", "num", "nro"}
_CPF_CONTEXT = re.compile(r"cpf\W{0,3}$", re.IGNORECASE)


def cpf_is_valid(digits: str) -> bool:
    if len(digits) != 11 or not digits.isdigit() or len(set(digits)) == 1:
        return False
    for size in (9, 10):
        total = sum(int(d) * (size + 1 - i) for i, d in enumerate(digits[:size]))
        check = (total * 10) % 11 % 10
        if check != int(digits[size]):
            return False
    return True


class PIIRedactor:
    def redact(self, text: str) -> RedactionResult:
        if not text:
            return RedactionResult(text, ())
        matches = sorted(self._find(text), key=lambda e: (e.start, -(e.end - e.start)))

        # Resolve sobreposições: fica a primeira entidade encontrada (padrões vêm em ordem
        # de prioridade e, empatando no início, a mais longa).
        chosen: list[PIIEntity] = []
        for entity in matches:
            if chosen and entity.start < chosen[-1].end:
                continue
            chosen.append(entity)

        parts: list[str] = []
        cursor = 0
        for entity in chosen:
            parts.append(text[cursor : entity.start])
            parts.append(f"[{entity.type.value}]")
            cursor = entity.end
        parts.append(text[cursor:])
        return RedactionResult("".join(parts), tuple(chosen))

    def _find(self, text: str) -> Iterator[PIIEntity]:
        taken: list[tuple[int, int]] = []

        def free(start: int, end: int) -> bool:
            return all(end <= s or start >= e for s, e in taken)

        def emit(type_: PIIType, m: re.Match[str], valid: bool | None = None) -> PIIEntity:
            taken.append((m.start(), m.end()))
            return PIIEntity(type_, m.start(), m.end(), valid)

        for m in _EMAIL.finditer(text):
            yield emit(PIIType.EMAIL, m)

        for m in _CPF_FORMATTED.finditer(text):
            if free(m.start(), m.end()):
                yield emit(PIIType.CPF, m, cpf_is_valid(re.sub(r"\D", "", m.group())))

        for m in _ELEVEN_DIGITS.finditer(text):
            if not free(m.start(), m.end()):
                continue
            digits = m.group()
            near_cpf_word = bool(_CPF_CONTEXT.search(text[max(0, m.start() - 8) : m.start()]))
            if near_cpf_word or cpf_is_valid(digits):
                yield emit(PIIType.CPF, m, cpf_is_valid(digits))
            # Senão fica para o padrão de telefone.

        for m in _TELEFONE.finditer(text):
            if free(m.start(), m.end()):
                yield emit(PIIType.TELEFONE, m)

        for m in _PLACA.finditer(text):
            if free(m.start(), m.end()) and m.group(1).lower() not in _PLACA_STOPWORDS:
                yield emit(PIIType.PLACA, m)


_DEFAULT = PIIRedactor()


def redact_text(text: str) -> str:
    """Atalho: só o texto mascarado."""
    return _DEFAULT.redact(text).text


def redact_value(value: Any, redact: Callable[[str], str] = redact_text) -> Any:
    """Mascara strings dentro de estruturas (dict, list, tuple), mantendo o resto."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_value(v, redact) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact_value(v, redact) for v in value)
    return value


def structlog_redactor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    """Processador do structlog: segunda linha de defesa. Mesmo que algum código esqueça de
    mascarar antes de logar, o dado pessoal não chega ao log."""
    return {key: redact_value(value) for key, value in event_dict.items()}
