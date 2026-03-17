from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src import models, schemas
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/registration-tokens", tags=["registration-tokens"])


def _generate_token() -> str:
    """Generate a registration token with ``glrt-`` prefix."""
    return f"glrt-{secrets.token_hex(20)}"


@router.post("")
async def create_registration_token(
    body: schemas.RegistrationTokenCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create a new registration token for worker enrollment."""
    token_str = _generate_token()
    name = body.name or f"token-{token_str[-8:]}"

    token = models.RegistrationToken(
        id=uuid.uuid4(),
        token=token_str,
        name=name,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)

    server_url = str(request.base_url).rstrip("/")

    return {
        "id": str(token.id),
        "token": token.token,
        "name": token.name,
        "created_at": token.created_at.isoformat(),
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        "revoked": token.revoked,
        "server_url": server_url,
    }


@router.get("", response_model=list[schemas.RegistrationTokenResponse])
async def list_registration_tokens(
    db: AsyncSession = Depends(get_db),
) -> list[models.RegistrationToken]:
    """List all registration tokens."""
    result = await db.execute(
        select(models.RegistrationToken).order_by(models.RegistrationToken.created_at.desc())
    )
    return list(result.scalars().all())


@router.delete("/{token_id}")
async def revoke_registration_token(
    token_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Revoke a registration token."""
    result = await db.execute(
        select(models.RegistrationToken).where(models.RegistrationToken.id == token_id)
    )
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=404, detail="Registration token not found")

    token.revoked = True
    await db.commit()
    return {"detail": "Token revoked", "token_id": str(token_id)}
