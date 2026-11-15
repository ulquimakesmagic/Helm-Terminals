"""Cross-chain route providers, tried in order (ROUTE_PROVIDERS, default "relay,debridge").

Helm shouldn't depend on a single bridge. Previews ask the providers in order and use the first that can quote the
route; the provider is recorded in the signed preview bounds, and confirmation re-quotes from that same provider only,
so what is sent is what was reviewed. Relay stays the default (it also handles same-chain conversions and carries
Helm's route fee as a Relay app fee); deBridge DLN covers cross-chain routes when Relay can't, or replaces it if
ROUTE_PROVIDERS lists it alone.

The object is used wherever Helm previously used the Relay client (`Executor.relay`), so its fee attributes describe
the default provider.
"""

from .base import AdapterError
from .debridge import PREFIX as DEBRIDGE_PREFIX


class RouteProviders:
    def __init__(self, providers: list) -> None:
        if not providers:
            raise ValueError("at least one route provider is required")
        self.providers = providers
        self.by_id = {p.id: p for p in providers}

    @property
    def primary(self):
        return self.providers[0]

    @property
    def id(self) -> str:
        return self.primary.id

    @property
    def label(self) -> str:
        return self.primary.label

    # Helm's route fee terms: the default provider's, as before.
    @property
    def fee_bps(self) -> int:
        return getattr(self.primary, "fee_bps", 0)

    @property
    def fee_recipient(self) -> str:
        return getattr(self.primary, "fee_recipient", "")

    @property
    def fee_note(self) -> str:
        return getattr(self.primary, "fee_note", None) or (
            f"{self.fee_bps / 100:g}% of the amount sent" if self.fee_bps else "none")

    def label_for(self, provider_id: str | None) -> str:
        p = self.by_id.get(provider_id or self.id)
        return p.label if p else str(provider_id)

    async def quote(self, *, provider: str | None = None, **kw) -> dict:
        if provider is not None:
            if provider not in self.by_id:
                raise AdapterError(f"Route provider {provider} isn't enabled on this server; request a new preview")
            return await self.by_id[provider].quote(**kw)
        problems = []
        for p in self.providers:
            if p.id == "debridge" and kw.get("from_network") == kw.get("to_network"):
                continue  # DLN is cross-chain only
            try:
                return await p.quote(**kw)
            except AdapterError as e:
                problems.append(str(e))
        if not problems:
            raise AdapterError("Same-chain conversions need Relay, which isn't enabled on this server")
        raise AdapterError(problems[0] if len(problems) == 1 else "No route provider could quote this: "
                           + "; ".join(problems))

    def _for_request(self, request_id: str):
        if request_id.startswith(DEBRIDGE_PREFIX):
            return self.by_id.get("debridge")
        return self.by_id.get("relay")

    async def status(self, request_id: str) -> dict:
        p = self._for_request(request_id)
        if p is None:
            raise AdapterError("The provider that carried this route is no longer enabled; check the destination wallet")
        return await p.status(request_id)

    def summarize(self, q: dict) -> dict:
        p = self.by_id.get(q.get("provider") or "relay") or self.primary
        out = p.summarize(q)
        out["provider_label"] = p.label
        return out

    # Relay-only bookkeeping (app fees held by Relay for Helm's treasury).
    async def app_fee_balances(self, wallet: str) -> dict:
        relay = self.by_id.get("relay")
        if relay is None:
            raise AdapterError("Relay isn't enabled on this server")
        return await relay.app_fee_balances(wallet)

    async def request(self, request_id: str) -> dict | None:
        relay = self.by_id.get("relay")
        return await relay.request(request_id) if relay is not None else None
