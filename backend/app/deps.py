"""FastAPI dependencies: settings, database, current user."""

from __future__ import annotations

from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from .config import Settings, get_settings
from .db import Database
from .security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_current_user(
    token: str = Depends(oauth2_scheme),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token, settings.secret_key)
    except jwt.PyJWTError as exc:
        raise credentials_error from exc
    user_id = payload.get("sub")
    if not user_id:
        raise credentials_error
    user = db.get_user(user_id)
    if not user:
        raise credentials_error
    return {"id": user["id"], "username": user["username"]}
