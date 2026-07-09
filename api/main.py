"""App FastAPI: piattaforma web multi-utente per il trading crypto continuo.

Avvio in sviluppo: `uvicorn api.main:app --reload` (porta 8000). In dev il
frontend gira separatamente con `npm run dev` (Vite, porta 5173) che fa da
proxy verso questo backend. Dopo `npm run build` nella cartella frontend/,
questo stesso processo serve anche i file statici della SPA da un'unica
porta, per un uso locale con un solo comando.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from api.deps import get_session_factory_dep
from api.routers import account, auth, session as session_router
from api.routers import settings as settings_router
from config.settings import settings
from orchestrator.session_manager import session_manager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Nessuna sessione live in-memory può essere sopravvissuta a un
    # (ri)avvio del processo: ogni TradingSession rimasta 'running' su DB va
    # riconciliata a 'interrupted'.
    session_manager.mark_interrupted_sessions_on_startup(get_session_factory_dep())
    yield


app = FastAPI(title="Intraday Trading Platform API", lifespan=lifespan)

app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax")

app.include_router(auth.router)
app.include_router(settings_router.router)
app.include_router(session_router.router)
app.include_router(account.router)

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
