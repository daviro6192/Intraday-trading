"""Dependency FastAPI condivise: sessione DB e utente corrente."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session, sessionmaker

from config.settings import settings
from storage.db import get_engine, get_session_factory, init_db

_engine = get_engine(settings.database_url)
init_db(_engine)
session_factory = get_session_factory(_engine)


def get_db() -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def get_session_factory_dep() -> sessionmaker[Session]:
    """Dependency (invece di un import diretto del modulo) così i test possono
    sovrascriverla con `app.dependency_overrides` per isolare il DB."""
    return session_factory
