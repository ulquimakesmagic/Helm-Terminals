import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from app.chains.solana import SYSTEM_PROGRAM, build_unsigned, find_ata, sign_versioned, sol_transfer_ix
from app.security import derive_fernet_key
from app.services.preflight import build_preflight, platform_fee_lamports, preflight_hash
from app.services.transparency import compute_transparency
from app.signing.base import SigningError
from app.signing.local import LocalDevSigner
from app.signing.turnkey import TurnkeySigner


def local_signer():
    return LocalDevSigner(derive_fernet_key("k"), "development")


async def test_local_signer_signs_and_verifies():
    s = local_signer()
    ref = await s.create_wallet("u", "solana", "w")
    sig = Signature.from_bytes(await s.sign_message(ref.signer_ref, b"hello"))
    assert sig.verify(Pubkey.from_string(ref.address), b"hello")


def test_local_signer_refuses_production():
    with pytest.raises(RuntimeError):
        LocalDevSigner(derive_fernet_key("k"), "production")


async def test_sign_versioned_fills_managed_and_ephemeral_signers():
    s = local_signer()
    ref = await s.create_wallet("u", "solana", "dev")
    dev = Pubkey.from_string(ref.address)
    mint = Keypair()
    ixs = [sol_transfer_ix(dev, dev, 1), Instruction(SYSTEM_PROGRAM, b"", [AccountMeta(mint.pubkey(), True, False)])]
    raw = build_unsigned(dev, ixs, str(Hash.default()))
    signed = await sign_versioned(raw, s, {ref.address: ref.signer_ref}, [mint])
    assert all(VersionedTransaction.from_bytes(signed).verify_with_results())


async def test_sign_versioned_rejects_unknown_signer():
    s = local_signer()
    stranger = Keypair().pubkey()
    raw = build_unsigned(stranger, [sol_transfer_ix(stranger, stranger, 1)], str(Hash.default()))
    with pytest.raises(SigningError):
        await sign_versioned(raw, s, {})


async def test_turnkey_stamp_and_sign_message():
    priv = ec.generate_private_key(ec.SECP256R1())
    scalar_hex = format(priv.private_numbers().private_value, "064x")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        seen["stamp"], seen["body"] = request.headers["X-Stamp"], body
        assert json.loads(body)["parameters"]["signWith"] == "pk_123"
        return httpx.Response(200, json={"activity": {"status": "ACTIVITY_STATUS_COMPLETED", "result": {
            "signRawPayloadResult": {"r": "11" * 32, "s": "22" * 32}}}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    t = TurnkeySigner("https://api.turnkey.test", "org", "02abcdef", scalar_hex, client=client)
    sig = await t.sign_message("pk_123", b"payload")
    assert sig == bytes.fromhex("11" * 32 + "22" * 32)

    stamp = json.loads(base64.urlsafe_b64decode(seen["stamp"] + "=" * (-len(seen["stamp"]) % 4)))
    assert stamp["scheme"] == "SIGNATURE_SCHEME_TK_API_P256" and stamp["publicKey"] == "02abcdef"
    priv.public_key().verify(bytes.fromhex(stamp["signature"]), seen["body"], ec.ECDSA(hashes.SHA256()))


async def test_turnkey_pending_activity_is_not_bypassed():
    priv = ec.generate_private_key(ec.SECP256R1())
    scalar_hex = format(priv.private_numbers().private_value, "064x")
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"activity": {"status": "ACTIVITY_STATUS_CONSENSUS_NEEDED"}})))
    t = TurnkeySigner("https://x", "org", "02ab", scalar_hex, client=client)
    with pytest.raises(SigningError):
        await t.sign_message("pk", b"m")


def test_preflight_hash_is_stable_and_sensitive():
    a = build_preflight("trade", {"amount": 1, "wallets": ["a"]})
    b = build_preflight("trade", {"wallets": ["a"], "amount": 1})
    c = build_preflight("trade", {"amount": 2, "wallets": ["a"]})
    assert a["preflight_hash"] == b["preflight_hash"] != c["preflight_hash"]
    assert preflight_hash({"x": 1}) == preflight_hash({"x": 1})


def test_platform_fee():
    assert platform_fee_lamports(1_000_000_000, 500) == 50_000_000
    assert platform_fee_lamports(1_000_000_000, 0) == 0
    assert platform_fee_lamports(-5, 500) == 0


def test_transparency_flags_concentration_and_marks_own_wallets():
    out = compute_transparency(
        total_supply=1_000_000, decimals=6,
        controlled={"me1": 200_000, "me2": 100_000},
        top_holders=[("me1", 200_000), ("other", 50_000)],
        warn_pct=20.0,
    )
    assert out["controlled_pct"] == pytest.approx(30.0)
    assert out["warnings"] and "30.0%" in out["warnings"][0]
    assert [h["is_yours"] for h in out["top_holders"]] == [True, False]


def test_transparency_quiet_below_threshold():
    out = compute_transparency(1_000_000, 6, {"me": 100_000}, [], 20.0)
    assert out["warnings"] == []


def test_find_ata_is_deterministic():
    o, m = Keypair().pubkey(), Keypair().pubkey()
    assert find_ata(o, m) == find_ata(o, m) != find_ata(m, o)


def test_database_url_is_normalised_for_async_driver():
    from app.config import Settings

    assert Settings(database_url="postgres://u:p@h:5432/d").database_url == "postgresql+asyncpg://u:p@h:5432/d"
    assert Settings(database_url="postgresql://u:p@h/d").database_url == "postgresql+asyncpg://u:p@h/d"
    assert Settings(database_url="postgresql+asyncpg://u:p@h/d").database_url == "postgresql+asyncpg://u:p@h/d"


def test_fee_transfers_are_spliced_into_a_lookup_table_transaction():
    """The fee must ride inside the launchpad's own transaction, which often loads accounts from lookup tables."""
    from solders.address_lookup_table_account import AddressLookupTableAccount
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction
    from solders.keypair import Keypair
    from solders.message import MessageV0
    from solders.pubkey import Pubkey
    from solders.signature import Signature
    from solders.system_program import decode_transfer
    from solders.transaction import VersionedTransaction

    from app.chains.solana import SYSTEM_PROGRAM, append_sol_transfers

    payer, prog, a, b, ro = Keypair(), Pubkey.new_unique(), Pubkey.new_unique(), Pubkey.new_unique(), Pubkey.new_unique()
    alt = AddressLookupTableAccount(Pubkey.new_unique(), [a, b])
    ix = Instruction(prog, b"\x01\x02", [AccountMeta(payer.pubkey(), True, True), AccountMeta(a, False, True),
                                         AccountMeta(b, False, False), AccountMeta(ro, False, False)])
    m = MessageV0.try_compile(payer.pubkey(), [ix], [alt], Hash.default())
    raw = bytes(VersionedTransaction.populate(m, [Signature.default()]))
    fee, ref = Pubkey.new_unique(), Pubkey.new_unique()

    m2 = VersionedTransaction.from_bytes(append_sol_transfers(raw, payer.pubkey(), [(fee, 1000), (ref, 250)])).message
    keys = list(m2.account_keys)
    lut = m2.address_table_lookups[0]
    loaded = [[a, b][i] for i in list(lut.writable_indexes) + list(lut.readonly_indexes)]
    res = lambda i: keys[i] if i < len(keys) else loaded[i - len(keys)]  # noqa: E731

    orig = m2.instructions[0]  # untouched apart from remapped indexes
    assert keys[orig.program_id_index] == prog and [res(i) for i in orig.accounts] == [payer.pubkey(), a, b, ro]
    for t, (dst, n) in zip(m2.instructions[1:], [(fee, 1000), (ref, 250)]):
        assert keys[t.program_id_index] == SYSTEM_PROGRAM and [res(i) for i in t.accounts] == [payer.pubkey(), dst]
        d = decode_transfer(Instruction(SYSTEM_PROGRAM, t.data, [AccountMeta(payer.pubkey(), True, True),
                                                                 AccountMeta(dst, False, True)]))
        assert d["lamports"] == n
    assert m2.is_maybe_writable(keys.index(fee)) and not m2.is_maybe_writable(keys.index(ro))
    assert not m2.is_maybe_writable(keys.index(SYSTEM_PROGRAM))
