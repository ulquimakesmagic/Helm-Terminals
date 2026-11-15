async def live_launch(client, headers, fake_rpc, holders=2):
    wallets = (await client.post("/wallets", json={"count": holders + 1, "label_prefix": "W"}, headers=headers)).json()
    dev, others = wallets[0], wallets[1:]
    created = await client.post("/launches", json={
        "name": "Test", "symbol": "tst", "dev_wallet_id": dev["id"], "dev_buy_sol": 0.5,
    }, headers=headers)
    assert created.status_code == 201, created.text
    launch_id = created.json()["id"]
    path = f"/launches/{launch_id}/deploy"
    preview = await client.post(path, json={}, headers=headers)
    deployed = await client.post(path, json={
        "confirm": True, "preflight_hash": preview.json()["preflight_hash"],
        "idempotency_key": f"deploy-{launch_id}",
    }, headers=headers)
    assert deployed.status_code == 200, deployed.text
    attached = await client.post(f"/launches/{launch_id}/wallets", json={
        "wallet_ids": [w["id"] for w in others],
    }, headers=headers)
    assert attached.status_code == 200, attached.text
    return launch_id, dev, others
