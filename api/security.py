"""Hashing password (bcrypt diretto, senza passlib: vedi nota sotto) e
dependency FastAPI per recuperare l'utente autenticato dalla sessione.

Nota: usiamo la libreria `bcrypt` direttamente invece di `passlib` perché
passlib non è più mantenuto dal 2020 e ha problemi di compatibilità noti con
le versioni recenti di bcrypt (rilevazione della versione basata su
attributi rimossi in bcrypt>=4.1).
"""

from __future__ import annotations

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from api.deps import get_db
from storage.models import User

_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    password_bytes = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.checkpw(password_bytes, password_hash.encode("utf-8"))


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Non autenticato")

    user = db.get(User, user_id)
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessione non valida")

    return user
