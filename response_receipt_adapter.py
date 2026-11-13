"""Offline-only trust-boundary adapter for an explicit model-mediated receipt.

This module is deliberately NOT registered as an MCP tool or HTTP endpoint.
The server integrating it MUST supply a resolver deriving an authenticated
principal from a trusted, verified transport context (never MCP tool args).
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, FrozenSet

from response_receipts import (
    DEFAULT_DB, get_receipt_status, record_receipt,
)

WRITE_SCOPE = "bridge:receipt:write"
READ_SCOPE = "bridge:receipt:read"


@dataclass(frozen=True)
class VerifiedIdentity:
    """Value returned only by a server-owned, verified auth-context resolver."""
    subject: str
    scopes: FrozenSet[str]


class ReceiptCallbackAdapter:
    """Explicitly-bound adapter; no implicit/anonymous authentication."""

    def __init__(self, identity_resolver: Callable[[object], VerifiedIdentity | None],
                 *, path: Path = DEFAULT_DB, enabled: bool = False):
        self._resolve_identity = identity_resolver
        self._path = Path(path)
        self._enabled = enabled

    def _identity(self, trusted_transport_context):
        try:
            identity = self._resolve_identity(trusted_transport_context)
            if not isinstance(identity, VerifiedIdentity):
                return None
            if not isinstance(identity.subject, str) or not identity.subject:
                return None
            if not isinstance(identity.scopes, frozenset):
                return None
            return identity
        except Exception:
            # Auth provider outage/error never permits a callback.
            return None

    def record(self, trusted_transport_context, model_arguments):
        """No principal, enable flag, path, or timestamp accepted from model."""
        if not self._enabled:
            return {"recorded": False, "reason": "disabled"}
        if (not isinstance(model_arguments, dict)
                or set(model_arguments.keys()) != {"event_id", "phrase"}):
            return {"recorded": False, "reason": "invalid_arguments"}
        identity = self._identity(trusted_transport_context)
        if identity is None or WRITE_SCOPE not in identity.scopes:
            return {"recorded": False, "reason": "not_authorized"}
        return record_receipt(
            model_arguments["event_id"], model_arguments["phrase"],
            authenticated_principal=identity.subject,
            enabled=True, path=self._path,
        )

    def status(self, trusted_transport_context, event_id):
        """Read only own receipt state; no arbitrary conversation text stored."""
        identity = self._identity(trusted_transport_context)
        if identity is None or READ_SCOPE not in identity.scopes:
            return {"status": "not_authorized"}
        return get_receipt_status(
            event_id, authenticated_principal=identity.subject,
            path=self._path,
        )