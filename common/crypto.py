"""Cifratura a riposo per credenziali con permessi operativi (es. API
key/secret di un exchange), a differenza di `api/security.py` che fa solo
hashing one-way delle password. Usato per le credenziali Binance Testnet
(vedi storage.models.UserSettings) — una API secret con permessi di trading
è più sensibile della sola chiave Anthropic (oggi salvata in chiaro), e lo
stesso meccanismo verrà riusato se/quando si passerà a un exchange reale.

Chiave derivata da SECRET_KEY (config.settings.settings.secret_key, già
usato per firmare i cookie di sessione) via SHA-256: un solo segreto da
gestire in .env, non due. Tradeoff accettato: ruotare SECRET_KEY invalida
ogni credenziale già salvata (non le password, hashate a parte con bcrypt;
non anthropic_api_key, in chiaro) — fallisce in modo esplicito e azionabile,
non silenzioso."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from config.settings import settings


def _fernet() -> Fernet:
    key_material = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key_material))


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "Impossibile decifrare le credenziali salvate: probabilmente SECRET_KEY è cambiato "
            "dall'ultimo salvataggio. Reinserisci le credenziali in Impostazioni."
        ) from exc
