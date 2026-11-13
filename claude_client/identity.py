"""Identity resolvers for the bridge shim (server side of the Muse client).

Security rule (mirrors event_queue.py in PR #9 and response_receipt_adapter.py
in PR #7): a caller's identity is derived ONLY from the transport context that
the hosting process supplies (an SSH/Tailscale connection, a local socket
peer, or a bearer token presented over that transport). Identity fields that
arrive inside an event payload or tool arguments are never trusted.

A resolver is a callable: resolver(trusted_context) -> Identity | None.
Returning None means "not authenticated"; every queue operation then fails
closed with ``not_authorized``.
"""
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path


@dataclass(frozen=True)
class Identity:
    """Field-compatible mirror of event_queue.VerifiedEventIdentity.

    When the real ``event_queue`` module is importable, ``as_queue_identity``
    converts to its class, because OfflineEventQueue checks ``isinstance``.
    """
    subject: str
    owner: str
    session: str
    source: str
    scopes: frozenset[str]

    def as_queue_identity(self):
        from event_queue import VerifiedEventIdentity
        return VerifiedEventIdentity(
            subject=self.subject, owner=self.owner, session=self.session,
            source=self.source, scopes=self.scopes)


def _convert(identity):
    """Return the queue module's identity class when it is importable.

    OfflineEventQueue (PR #9) validates identities with ``isinstance``
    against its own dataclass, so a resolver used with the real queue must
    hand it that exact class. With a duck-typed test double the local mirror
    class is returned unchanged.
    """
    if identity is None:
        return None
    try:
        return identity.as_queue_identity()
    except Exception:
        return identity


def _check_private_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("registry path must be a regular non-symlink file")
    st = path.stat()
    if st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise PermissionError("registry must be owned by the process, mode 0600")
    return path


class TokenRegistryResolver:
    """Resolve identity from a bearer token presented by the transport.

    The registry is an operator-provisioned JSON file (mode 0600) mapping
    ``sha256(token)`` hex digests to identity records. Raw tokens are never
    stored in the registry and never logged. The token itself must travel
    over an authenticated/encrypted transport (Tailscale, SSH); this resolver
    only decides which principal a presented token represents.

    trusted_context shape: {"presented_token": "<token string>"}
    """

    def __init__(self, registry_path):
        path = _check_private_file(registry_path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("registry must be a JSON object")
        self._entries = {}
        for digest, record in raw.items():
            if not isinstance(record, dict):
                raise ValueError("registry entries must be objects")
            self._entries[str(digest).lower()] = Identity(
                subject=str(record["subject"]),
                owner=str(record["owner"]),
                session=str(record["session"]),
                source=str(record["source"]),
                scopes=frozenset(str(s) for s in record["scopes"]),
            )

    def __call__(self, trusted_context):
        if not isinstance(trusted_context, dict):
            return None
        token = trusted_context.get("presented_token")
        if type(token) is not str or not token:
            return None
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        for known_digest, identity in self._entries.items():
            if hmac.compare_digest(known_digest, digest):
                return _convert(identity)
        return None


class TailscaleContextResolver:
    """Resolve identity from Tailscale connection metadata.

    Intended context source: the shim host obtains the peer's Tailscale
    identity (login name and node name, e.g. via ``tailscale whois`` on the
    connection's remote address, or the remote user/device that tailscaled
    reports for an SSH session) and passes it here. Payload data is never
    consulted. The (user, device) -> identity mapping is operator-provisioned
    in code/config on the bridge host, never supplied by the caller.

    trusted_context shape: {"tailscale_user": "...", "tailscale_device": "..."}
    """

    def __init__(self, mapping):
        # mapping: {(user, device): Identity}
        self._mapping = dict(mapping)

    def __call__(self, trusted_context):
        if not isinstance(trusted_context, dict):
            return None
        key = (trusted_context.get("tailscale_user"),
               trusted_context.get("tailscale_device"))
        if not all(type(v) is str and v for v in key):
            return None
        return _convert(self._mapping.get(key))
