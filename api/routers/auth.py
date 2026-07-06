"""Registrazione, login, logout e utente corrente."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import get_db
from api.schemas import LoginRequest, RegisterRequest, UserResponse
from api.security import get_current_user, hash_password, verify_password
from config.settings import trading_config
from storage.models import User, UserSettings

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> User:
    existing = db.scalar(select(User).where(User.username == body.username))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username già in uso")

    user = User(username=body.username, password_hash=hash_password(body.password))
    db.add(user)
    db.flush()  # per ottenere user.id

    user.settings = UserSettings(
        user_id=user.id,
        watchlists_json=json.dumps(trading_config["watchlists"]),
        risk_limits_json=json.dumps(trading_config["risk_limits"]),
        news_feeds_json=json.dumps(trading_config["news_feeds"]),
    )
    db.add(user.settings)
    db.commit()
    db.refresh(user)

    request.session["user_id"] = user.id
    return user


@router.post("/login", response_model=UserResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> User:
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenziali non valide")

    request.session["user_id"] = user.id
    return user


@router.post("/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)) -> User:
    return user
