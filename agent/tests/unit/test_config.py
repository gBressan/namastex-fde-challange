from __future__ import annotations

import pytest

from autoseguro.config import Settings


@pytest.mark.parametrize(
    ("database_url", "expected"),
    [
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ],
)
def test_sqlalchemy_url_deriva_do_database_url(database_url: str, expected: str) -> None:
    settings = Settings(database_url=database_url, _env_file=None)
    assert settings.sqlalchemy_url == expected


def test_chave_do_llm_nao_aparece_no_repr() -> None:
    settings = Settings(llm_api_key="hf_segredo", _env_file=None)
    assert "hf_segredo" not in repr(settings)
