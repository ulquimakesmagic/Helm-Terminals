"""Owner-only presentation preferences and encrypted custom performance-card media."""
import asyncio
import struct
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import web_user
from ..models import PerformanceCard, User
from ..security import _fernet
from ..services.guard import ensure_not_locked, record_attempt
from ..services.performance_media import MAX_BYTES, inspect_media

router = APIRouter(prefix='/profile/performance', tags=['performance'])
_uploads = asyncio.Semaphore(2)
DEFAULTS = {'theme':'orbit','show_name':True,'show_amount':True,'show_token':True,'custom_background':False,
            'position_x':50,'position_y':50,'zoom':1,'overlay':0.65}


class Preferences(BaseModel):
    theme: Literal['orbit','aurora','carbon'] = 'orbit'
    show_name: bool = True
    show_amount: bool = True
    show_token: bool = True
    custom_background: bool = False
    position_x: int = Field(default=50, ge=0, le=100)
    position_y: int = Field(default=50, ge=0, le=100)
    zoom: float = Field(default=1, ge=1, le=2.5)
    overlay: float = Field(default=0.65, ge=0.5, le=0.95)


def out(row):
    return {'preferences':{**DEFAULTS, **(row.preferences if row else {})},
            'media':row.media_info if row and row.media_cipher else None}


async def get_or_create(db, user):
    row = await db.get(PerformanceCard,user.id)
    if row is None:
        row=PerformanceCard(user_id=user.id,preferences=dict(DEFAULTS)); db.add(row); await db.flush()
    return row


@router.get('')
async def get_preferences(user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    return out(await db.get(PerformanceCard,user.id))


@router.put('')
async def put_preferences(body: Preferences, user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    row=await get_or_create(db,user)
    if body.custom_background and not row.media_cipher: raise HTTPException(400,'Upload a background first')
    row.preferences=body.model_dump(); await db.commit(); return out(row)


@router.put('/media')
async def upload_media(request: Request, user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    await ensure_not_locked(db,f'performance-upload:{user.id}',6)
    await record_attempt(db,f'performance-upload:{user.id}')
    async with _uploads:
        data=bytearray()
        async for chunk in request.stream():
            if len(data)+len(chunk)>MAX_BYTES: raise HTTPException(413,'Backgrounds must be at most 10 MB')
            data.extend(chunk)
        try: info=inspect_media(bytes(data))
        except (ValueError,struct.error) as exc: raise HTTPException(400,str(exc)) from None
        row=await get_or_create(db,user)
        row.media_cipher=_fernet().encrypt(bytes(data)); row.media_info={**info,'bytes':len(data)}
        row.preferences={**DEFAULTS,**row.preferences,'custom_background':True}
        await db.commit(); return out(row)


@router.get('/media')
async def get_media(user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    row=await db.get(PerformanceCard,user.id)
    if not row or not row.media_cipher: raise HTTPException(404,'No custom background')
    return Response(_fernet().decrypt(row.media_cipher),media_type=row.media_info['mime'],headers={
        'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff',
        'Content-Security-Policy':"default-src 'none'; sandbox",'Content-Disposition':'inline'})


@router.delete('/media')
async def delete_media(user: User = Depends(web_user), db: AsyncSession = Depends(get_db)):
    row=await db.get(PerformanceCard,user.id)
    if row:
        row.media_cipher=None; row.media_info=None
        row.preferences={**DEFAULTS,**row.preferences,'custom_background':False}; await db.commit()
    return out(row)
