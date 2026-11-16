from sqlalchemy import select
from app.db import get_sessionmaker
from app.models import Referral, ReferralLink, User
from .conftest import signup, TEST_PW


async def test_username_aliases_preserve_ownership_across_renames(client):
    headers=await signup(client,'nickname-owner@example.com',main=False)
    uid=(await client.get('/auth/me',headers=headers)).json()['id']
    assert (await client.post('/referrals',headers=headers,json={})).status_code==409
    async with get_sessionmaker()() as db:
        db.add(Referral(owner_user_id=uid,code='LEGACY123',uses=0,max_uses=1000));await db.commit()
    username='captain_long_name_01'
    changed=await client.patch('/profile',headers=headers,json={'handle':username})
    assert changed.status_code==200, changed.text
    code=(await client.post('/referrals',headers=headers,json={})).json()['code']
    assert code==username and len(code)>16
    again=(await client.post('/referrals',headers=headers,json={})).json()['code']
    assert again==code
    assert (await client.patch('/profile',headers=headers,json={'handle':'captain_two'})).status_code==200
    summary=(await client.get('/referrals',headers=headers)).json()
    assert summary['username_code']=='captain_two' and len(summary['codes'])==3
    other=await signup(client,'nickname-other@example.com',main=False)
    assert (await client.patch('/profile',headers=other,json={'handle':username})).status_code==409
    for i,code in enumerate([username.upper(),'LEGACY123','captain_two']):
        response=await client.post('/auth/register',json={'email':f'invited-{i}@example.com','password':TEST_PW,'referral_code':code})
        assert response.status_code==201, response.text
    async with get_sessionmaker()() as db:
        links=(await db.execute(select(ReferralLink))).scalars().all()
        assert len(links)==3 and all(link.referrer_user_id==uid for link in links)
