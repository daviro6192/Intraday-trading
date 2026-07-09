"""Cifratura a riposo delle credenziali con permessi operativi (es. API
secret di un exchange): round-trip, stringa vuota, e fallimento esplicito
se SECRET_KEY cambia tra un salvataggio e l'altro."""

from __future__ import annotations

import pytest

from common.crypto import decrypt_secret, encrypt_secret
from config.settings import settings


def test_round_trip():
    token = encrypt_secret("un-segreto-molto-sensibile")
    assert token != "un-segreto-molto-sensibile"  # non deve mai essere in chiaro
    assert decrypt_secret(token) == "un-segreto-molto-sensibile"


def test_empty_string_passthrough():
    assert encrypt_secret("") == ""
    assert decrypt_secret("") == ""


def test_decrypt_fails_loudly_if_secret_key_changes(monkeypatch):
    token = encrypt_secret("un-segreto")
    monkeypatch.setattr(settings, "secret_key", "una-chiave-completamente-diversa")

    with pytest.raises(ValueError, match="Impossibile decifrare"):
        decrypt_secret(token)
