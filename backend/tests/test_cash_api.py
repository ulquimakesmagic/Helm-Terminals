from solders.transaction import VersionedTransaction

from .conftest import signup
from .test_api import enable_2fa, totp_code


async def test_usdc_withdraw_preserves_2fa_and_gross_debit(client, fake_rpc):
    h = await signup(client)
    source, dest = (await client.post("/wallets", headers=h, json={"count": 2})).json()
    fake_rpc.tokens[source["address"]] = 5_000_000
    fake_rpc.tokens[dest["address"]] = 0
    body = {"wallet_ids": [source["id"]], "destination": dest["address"],
            "asset": "stable", "all": False, "amount": 2}
    preview = await client.post("/wallets/withdraw", headers=h, json=body)
    assert preview.status_code == 200
    assert preview.json()["requires_2fa"]
    assert any("Enable 2FA" in warning for warning in preview.json()["preflight"]["warnings"])
    secret = await enable_2fa(client, h)
    preview = (await client.post("/wallets/withdraw", headers=h, json=body)).json()
    assert preview["preflight"]["asset"] == "USDC"
    assert preview["preflight"]["from"][0]["amount"] == 2
    confirmation = {**body, "confirm": True, "idempotency_key": "usdc-withdraw",
                    "preflight_hash": preview["preflight_hash"]}
    response = await client.post("/wallets/withdraw", headers=h, json=confirmation)
    assert response.status_code == 401 and not fake_rpc.sent
    response = await client.post("/wallets/withdraw", headers=h,
        json={**confirmation, "totp": await totp_code(secret)})
    assert response.status_code == 200, response.text
    job = (await client.get('/jobs/' + response.json()["job_id"], headers=h)).json()
    assert job["status"] == "confirmed", job
    msg = VersionedTransaction.from_bytes(fake_rpc.sent[-1]).message
    assert len(msg.instructions) == 1
    assert int.from_bytes(msg.instructions[0].data[1:9], "little") == 2_000_000
    replay = await client.post("/wallets/withdraw", headers=h, json=confirmation)
    assert replay.status_code == 200 and len(fake_rpc.sent) == 1


async def test_withdraw_rejects_duplicate_wallets_and_fractional_base_units(client, fake_rpc):
    h = await signup(client)
    source, dest = (await client.post("/wallets", headers=h, json={"count": 2})).json()
    body = {"wallet_ids": [source["id"], source["id"]], "destination": dest["address"], "asset": "stable"}
    assert (await client.post("/wallets/withdraw", headers=h, json=body)).status_code == 400
    body.update(wallet_ids=[source["id"]], all=False, amount=0.0000001)
    assert (await client.post("/wallets/withdraw", headers=h, json=body)).status_code == 400
    assert not fake_rpc.sent
