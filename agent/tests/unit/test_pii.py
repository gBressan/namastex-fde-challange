from __future__ import annotations

import io
import json
import uuid

import pytest
import structlog

from autoseguro.log import configure_logging
from autoseguro.safety.pii import PIIRedactor, PIIType, cpf_is_valid, redact_text, redact_value

redactor = PIIRedactor()

# CPFs com dígito verificador válido (gerados pela mesma regra do dataset).
CPF_OK = "389.083.863-43"
CPF_OK_DIGITS = "38908386343"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # CPF
        (f"cpf {CPF_OK}", "cpf [CPF]"),
        (f"Tenho 35 anos, cep 26703-384, cpf {CPF_OK}", "Tenho 35 anos, cep 26703-384, cpf [CPF]"),
        (f"meu cpf é {CPF_OK_DIGITS}", "meu cpf é [CPF]"),
        ("389 083 863 43", "[CPF]"),
        ("cpf: 123.456.789-00 (acho que errei)", "cpf: [CPF] (acho que errei)"),
        (f"cpf:{CPF_OK_DIGITS}", "cpf:[CPF]"),
        # Telefone
        ("o whats é esse mesmo +55 47 98646-8699", "o whats é esse mesmo [TELEFONE]"),
        ("liga no (21) 97224-2584", "liga no [TELEFONE]"),
        ("21 97224-2584", "[TELEFONE]"),
        ("21972242584", "[TELEFONE]"),
        ("+5521972242584", "[TELEFONE]"),
        ("fixo 21 3333-4444", "fixo [TELEFONE]"),
        ("98646-8699 é meu cel", "[TELEFONE] é meu cel"),
        # E-mail
        ("meu email é sabrinamartins24@yahoo.com.br", "meu email é [EMAIL]"),
        ("ana.souza+seguro@gmail.com", "[EMAIL]"),
        ("Email: JOAO_SILVA@Outlook.com.", "Email: [EMAIL]."),
        # Placa
        ("a placa é WKZ8T81 se precisar", "a placa é [PLACA] se precisar"),
        ("placa abc1d23", "placa [PLACA]"),
        ("placa antiga ABC-1234", "placa antiga [PLACA]"),
        ("ABC1234", "[PLACA]"),
        # Vários na mesma mensagem
        (
            "meu email é ursula.souza@gmail.com e o whats é esse mesmo +55 21 97224-2584",
            "meu email é [EMAIL] e o whats é esse mesmo [TELEFONE]",
        ),
        (
            f"cpf {CPF_OK}, placa WKZ8T81, tel (11) 91234-5678",
            "cpf [CPF], placa [PLACA], tel [TELEFONE]",
        ),
    ],
)
def test_mascara_dados_pessoais(text: str, expected: str) -> None:
    assert redact_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "cep 26703-384",
        "CEP 01310100",
        "(21) 310-100",  # CEP escrito de um jeito estranho não vira telefone
        "Toyota Corolla 2008",
        "e um Sandero 2022",
        "ano2022",
        "tenho 35 anos",
        "Pelo perfil consigo o plano Premium por R$ 219,90/mes.",
        "franquia de R$ 4.500,00",
        "começa em 15/11/2026",
        "[documento] CNH_frente.pdf",
        "[audio] mensagem de voz (18s)",
        "quote_id=f390c112-432b-48e7-b3d5-6bce3ed6b000",
        "conv_00042",
        "Oi, queria fazer um seguro pro meu carro",
    ],
)
def test_nao_mascara_o_que_nao_e_dado_pessoal(text: str) -> None:
    assert redact_text(text) == text


def test_uuid_aleatorios_nao_viram_telefone_nem_cpf() -> None:
    for _ in range(500):
        value = str(uuid.uuid4())
        assert redact_text(value) == value


def test_entidades_guardam_tipo_e_posicao_mas_nao_o_valor() -> None:
    text = f"cpf {CPF_OK} e email a@b.com"
    result = redactor.redact(text)

    assert [e.type for e in result.entities] == [PIIType.CPF, PIIType.EMAIL]
    cpf = result.entities[0]
    assert text[cpf.start : cpf.end] == CPF_OK
    assert cpf.valid is True
    assert CPF_OK not in repr(result.entities)


def test_cpf_com_digito_errado_e_mascarado_mas_marcado_invalido() -> None:
    result = redactor.redact("cpf 123.456.789-00")
    assert result.text == "cpf [CPF]"
    assert result.entities[0].valid is False


def test_mascarar_duas_vezes_da_o_mesmo_resultado() -> None:
    text = f"cpf {CPF_OK}, whats +55 21 97224-2584, placa WKZ8T81, a@b.com"
    once = redact_text(text)
    assert redact_text(once) == once


@pytest.mark.parametrize(
    ("digits", "valid"),
    [(CPF_OK_DIGITS, True), ("12345678909", True), ("12345678900", False), ("11111111111", False)],
)
def test_cpf_is_valid(digits: str, valid: bool) -> None:
    assert cpf_is_valid(digits) is valid


def test_redact_value_percorre_estruturas() -> None:
    value = {"msg": "cpf 389.083.863-43", "itens": ["a@b.com", 42], "ok": True}
    assert redact_value(value) == {"msg": "cpf [CPF]", "itens": ["[EMAIL]", 42], "ok": True}


def test_log_nunca_mostra_dado_pessoal() -> None:
    buffer = io.StringIO()
    configure_logging("INFO", "json")
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=buffer))
    try:
        structlog.get_logger().info(
            "mensagem_recebida", body=f"meu cpf é {CPF_OK}", extra={"email": "a@b.com"}
        )
    finally:
        structlog.reset_defaults()

    line = json.loads(buffer.getvalue())
    assert line["body"] == "meu cpf é [CPF]"
    assert line["extra"] == {"email": "[EMAIL]"}
    assert CPF_OK not in buffer.getvalue()
