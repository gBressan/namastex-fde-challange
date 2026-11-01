"""Camada Silver mínima: mensagens de lead ordenadas, mascaradas e com gabarito.

- **Bronze** é o `dataset/conversations.parquet` original, intocado.
- **Silver** (este módulo): só mensagens de lead, na ordem de `message_index` (os timestamps
  do dataset estão fora de ordem), com dados pessoais mascarados e o nome do remetente
  trocado por um pseudônimo. Leva junto o gabarito usado para avaliar a extração do LLM.

Roda em DuckDB, dentro do processo: o `PIIRedactor` vira uma função SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
from duckdb.sqltypes import VARCHAR

from autoseguro.safety.pii import PIIRedactor, PIIType, redact_text

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DATASET = REPO_ROOT / "dataset" / "conversations.parquet"
DEFAULT_SILVER = REPO_ROOT / "data" / "silver" / "lead_messages.parquet"

SILVER_SQL = """
SELECT
    conversation_id,
    message_index,
    message_type,
    'lead_' || left(sha256($salt || sender_name), 8)       AS sender_pseudonym,
    redact(message_body)                                    AS body_redacted,
    conversation_outcome,
    lead_idade_informada                                    AS gt_idade,
    CAST(right(veiculo_texto, 4) AS INTEGER)                AS gt_veiculo_ano,
    -- O CEP vem solto no texto ("cep 26703-384"); guardamos só o prefixo, que define a região.
    max(nullif(regexp_extract(message_body, '(?i)cep\\D{0,3}(\\d{2})\\d{3}-?\\d{3}', 1), ''))
        OVER (PARTITION BY conversation_id)                 AS gt_cep_prefixo
FROM read_parquet($dataset)
WHERE sender_role = 'lead'
ORDER BY conversation_id, message_index
"""


@dataclass(frozen=True)
class SilverStats:
    messages: int
    conversations: int
    masked: dict[PIIType, int]


def build_silver(
    dataset: Path = DEFAULT_DATASET, out: Path = DEFAULT_SILVER, *, salt: str
) -> SilverStats:
    out.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.create_function("redact", redact_text, [VARCHAR], VARCHAR)
        relation = con.sql(SILVER_SQL, params={"salt": salt, "dataset": str(dataset)})
        relation.write_parquet(str(out))

        counts = con.execute(
            """
            SELECT count(*), count(DISTINCT conversation_id),
                   sum(len(body_redacted) - len(replace(body_redacted, '[CPF]', ''))) // 5,
                   sum(len(body_redacted) - len(replace(body_redacted, '[TELEFONE]', ''))) // 10,
                   sum(len(body_redacted) - len(replace(body_redacted, '[EMAIL]', ''))) // 7,
                   sum(len(body_redacted) - len(replace(body_redacted, '[PLACA]', ''))) // 7
            FROM read_parquet(?)
            """,
            [str(out)],
        ).fetchone()
    finally:
        con.close()

    assert counts is not None
    messages, conversations, cpf, telefone, email, placa = counts
    return SilverStats(
        messages=messages,
        conversations=conversations,
        masked={
            PIIType.CPF: cpf,
            PIIType.TELEFONE: telefone,
            PIIType.EMAIL: email,
            PIIType.PLACA: placa,
        },
    )


def find_leaks(silver: Path = DEFAULT_SILVER) -> list[tuple[str, int, set[PIIType]]]:
    """Procura dados pessoais que tenham escapado no texto mascarado. Lista vazia = ok."""
    redactor = PIIRedactor()
    con = duckdb.connect()
    try:
        rows = con.execute(
            "SELECT conversation_id, message_index, body_redacted FROM read_parquet(?)",
            [str(silver)],
        ).fetchall()
    finally:
        con.close()
    leaks = []
    for conversation_id, message_index, body in rows:
        found = redactor.redact(body).types()
        if found:
            leaks.append((conversation_id, message_index, found))
    return leaks
