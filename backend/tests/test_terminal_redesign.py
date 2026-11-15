import struct
import zlib

import pytest

from .conftest import signup


def png(width=64,height=64):
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\0'*width*3)*height))+chunk(b'IEND',b'')


async def test_retired_routes_cannot_create_or_resume_trades(client):
    h=await signup(client,'retired@example.com')
    for method,path in [('GET','/automations'),('POST','/launches/old/automations'),('POST','/automations/old/resume'),('PATCH','/automations/old')]:
        response=await client.request(method,path,headers=h,json={})
        assert response.status_code==404,(path,response.text)


async def test_stale_scheduled_callback_never_reaches_a_signer(client):
    from sqlalchemy import select
    from app.db import get_sessionmaker
    from app.deps import run_job
    from app.models import Job, User
    await signup(client,'stale-callback@example.com')
    async with get_sessionmaker()() as db:
        user=(await db.execute(select(User).where(User.email=='stale-callback@example.com'))).scalar_one()
        job=Job(user_id=user.id,kind='scheduled_buy',status='pending')
        db.add(job);await db.commit();identifier=job.id
    called=[]
    async def forbidden(*args):
        called.append(True)
    await run_job(identifier,forbidden)
    assert not called
    async with get_sessionmaker()() as db:
        assert (await db.get(Job,identifier)).status=='failed'


def test_distribution_amounts_preserve_base_units_and_refuse_rounding():
    from decimal import Decimal
    from fastapi import HTTPException
    from app.routes.distribute import _to_raw
    from app.services.preflight import preflight_hash
    assert _to_raw(Decimal('1.000000000000000001'),18)==10**18+1
    with pytest.raises(HTTPException):_to_raw(Decimal('0.0000000001'),9)
    assert preflight_hash({'amount_raw':str(10**18)})!=preflight_hash({'amount_raw':str(10**18+1)})


async def test_profiles_are_private_even_when_old_client_requests_publication(client):
    h=await signup(client,'private@example.com')
    assert (await client.patch('/profile',headers=h,json={'display_name':'Private trader','handle':'private_trader'})).status_code==200
    assert (await client.patch('/profile',headers=h,json={'is_public':True})).status_code==400
    assert (await client.get('/u/private_trader')).status_code==404
    profile=(await client.get('/profile',headers=h)).json()['profile']
    assert profile['is_public'] is False and profile['public_path'] is None


async def test_backgrounds_are_owner_only_encrypted_and_removable(client):
    from app.db import get_sessionmaker
    from app.models import PerformanceCard
    from sqlalchemy import select
    a,b=await signup(client,'media-a@example.com'),await signup(client,'media-b@example.com')
    image=png()
    assert (await client.put('/profile/performance/media',content=image)).status_code==401
    response=await client.put('/profile/performance/media',headers={**a,'Content-Type':'image/png'},content=image)
    assert response.status_code==200,response.text
    assert response.json()['media']['width']==64
    async with get_sessionmaker()() as db:
        row=(await db.execute(select(PerformanceCard))).scalar_one()
        assert row.media_cipher!=image and image not in row.media_cipher
    assert (await client.get('/profile/performance/media',headers=b)).status_code==404
    assert (await client.get('/profile/performance/media')).status_code==401
    response=await client.get('/profile/performance/media',headers=a)
    assert response.content==image and response.headers['cache-control']=='private, no-store'
    assert 'image/png' in response.headers['content-type']
    assert (await client.delete('/profile/performance/media',headers=a)).json()['media'] is None
    assert (await client.get('/profile/performance/media',headers=a)).status_code==404


async def test_background_validation_rejects_executable_and_malformed_content(client):
    h=await signup(client,'media-validation@example.com')
    for image in [b'<svg onload="alert(1)"/>',b'<html>not an image</html>',png(4097,64),png()[:-3]]:
        response=await client.put('/profile/performance/media',headers={**h,'Content-Type':'image/png'},content=image)
        assert response.status_code==400,response.text
    response=await client.put('/profile/performance/media',headers=h,content=b'x'*(10*1024*1024+1))
    assert response.status_code==413


async def test_performance_preferences_validate_and_remain_private(client):
    a,b=await signup(client,'prefs-a@example.com'),await signup(client,'prefs-b@example.com')
    assert (await client.put('/profile/performance',headers=a,json={'theme':'javascript:alert(1)'})).status_code==422
    assert (await client.put('/profile/performance',headers=a,json={'theme':'orbit','custom_background':True})).status_code==400
    assert (await client.put('/profile/performance',headers=a,json={'theme':'carbon','show_name':False,'show_amount':False})).status_code==200
    assert (await client.get('/profile/performance',headers=b)).json()['preferences']['theme']=='orbit'
    assert (await client.get('/profile/performance',headers=a)).json()['preferences']['show_amount'] is False


@pytest.mark.parametrize('version',[0,1])
def test_mp4_duration_bounds(version):
    from app.services.performance_media import inspect_media
    def atom(name,data):return struct.pack('>I',len(data)+8)+name+data
    def movie(seconds):
        header=bytes([version])+b'\0'*(19 if version else 11)+struct.pack('>I',1000)+(seconds*1000).to_bytes(8 if version else 4,'big')
        track=b'\0'*76+struct.pack('>II',1200<<16,630<<16)
        return atom(b'ftyp',b'isom\0\0\0\0isom')+atom(b'moov',atom(b'mvhd',header)+atom(b'trak',atom(b'tkhd',track)+atom(b'mdia',atom(b'hdlr',b'\0'*8+b'vide'))))+atom(b'mdat',b'\0'*4)
    assert inspect_media(movie(3))['duration']==3
    with pytest.raises(ValueError):inspect_media(movie(31))


async def test_attachment_refuses_other_chain_and_duplicate_membership(client,fake_rpc):
    from .launch_fixture import live_launch
    h=await signup(client,'attachment@example.com')
    lid,dev,wallets=await live_launch(client,h,fake_rpc)
    others=(await client.post('/wallets',headers=h,json={'network':'base','count':1})).json()
    response=await client.post(f'/launches/{lid}/wallets',headers=h,json={'wallet_ids':[others[0]['id']]})
    assert response.status_code==400 and 'chain' in response.json()['detail']
    assert (await client.post(f'/launches/{lid}/wallets',headers=h,json={'wallet_ids':[wallets[0]['id'],wallets[0]['id']]})).json()['attached']==0
    response=await client.post(f'/launches/{lid}/wallets',headers=h,json={'wallet_ids':[wallets[0]['id']],'role':'dev'})
    assert response.status_code==400
