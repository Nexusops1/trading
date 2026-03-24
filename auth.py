"""NEXUS shared JWT auth — validates tokens from the Gateway cookie or Bearer header."""

import os
from fastapi import Request, HTTPException
from jose import jwt, JWTError

JWT_SECRET = os.environ.get("NEXUS_JWT_SECRET", "")
JWT_ALGORITHM = "HS256"


def verify_jwt(request: Request) -> dict:
    """Validate JWT from cookie or Authorization header.
    Returns decoded payload or raises 401."""
    token = request.cookies.get("nexus_session")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if "sub" not in payload or "exp" not in payload or "iss" not in payload:
            raise HTTPException(status_code=401, detail="invalid token")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="invalid token")
