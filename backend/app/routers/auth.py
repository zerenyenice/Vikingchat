"""Register / login / me."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..db import Database
from ..deps import get_current_user, get_db
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict[str, Any]


def _issue(settings: Settings, user: dict[str, Any]) -> TokenResponse:
    token = create_access_token(
        user["id"], settings.secret_key, settings.access_token_expire_minutes, {"username": user["username"]}
    )
    return TokenResponse(access_token=token, user={"id": user["id"], "username": user["username"]})


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(
    body: RegisterRequest,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> TokenResponse:
    if not settings.allow_registration:
        raise HTTPException(status_code=403, detail="Registration is disabled")
    if not USERNAME_RE.match(body.username):
        raise HTTPException(
            status_code=422, detail="Username may contain letters, digits, '.', '_' and '-' (3-32 chars)"
        )
    if db.get_user_by_username(body.username):
        raise HTTPException(status_code=409, detail="Username already taken")
    user = db.create_user(body.username, hash_password(body.password))
    return _issue(settings, user)


@router.post("/login", response_model=TokenResponse)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> TokenResponse:
    user = db.get_user_by_username(form.username)
    if not user or not verify_password(form.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    return _issue(settings, user)


@router.get("/me")
def me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return user
