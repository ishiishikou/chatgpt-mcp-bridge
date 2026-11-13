"""Bridge shim: the server-side adapter a Muse client talks to.

The shim wraps an event-queue object with the PR #9 interface
(``authorize``/``enqueue``/``claim``/``report``/``cancel``/``status``) and an
identity resolver. Two boundaries are enforced here, in addition to the
queue's own checks:

1. Identity comes only from the *transport context* fixed by the hosting
   process (see identity.py). A request line/params object that tries to
   smuggle its own ``context`` (or identity fields) is rejected fail-closed.
2. ``authorize`` and ``claim``/``report`` are NOT exposed to the external
   client surface. Authorization is a trusted operator step on the bridge
   side, and claim/report belong to the guarded UI / host side. The client
   surface is exactly: ``enqueue``, ``cancel``, ``status``, ``receipt_status``.
"""
import json

CLIENT_METHODS = frozenset({"enqueue", "cancel", "status", "receipt_status"})
_FORBIDDEN_PARAM_KEYS = frozenset({
    "context", "subject", "owner", "session", "source", "scopes", "identity",
})


class BridgeShim:
    """Queue + resolver adapter with a deliberately narrow client surface."""

    def __init__(self, queue, resolver, *, receipt_status_fn=None):
        if not callable(resolver):
            raise TypeError("resolver must be callable")
        self._queue = queue
        self._resolver = resolver
        self._receipt_status_fn = receipt_status_fn

    def _identity(self, context):
        try:
            return self._resolver(context)
        except Exception:
            return None

    def handle(self, context, method, params):
        """Handle one client request. ``context`` is transport-supplied only."""
        if method not in CLIENT_METHODS:
            return {"ok": False, "reason": "method_not_exposed"}
        if not isinstance(params, dict):
            return {"ok": False, "reason": "invalid_arguments"}
        if _FORBIDDEN_PARAM_KEYS & set(params):
            # Identity must never arrive inside the payload.
            return {"ok": False, "reason": "identity_in_payload"}
        if self._identity(context) is None:
            return {"ok": False, "reason": "not_authorized"}
        event_id = params.get("event_id")
        try:
            if method == "enqueue":
                result = self._queue.enqueue(context, event_id, params.get("message"))
            elif method == "cancel":
                result = self._queue.cancel(context, event_id)
            elif method == "status":
                result = self._queue.status(context, event_id)
            else:  # receipt_status
                if self._receipt_status_fn is None:
                    return {"ok": False, "reason": "receipt_unavailable"}
                result = self._receipt_status_fn(context, event_id)
        except Exception:
            return {"ok": False, "reason": "shim_error"}
        return {"ok": True, "result": result}


def serve_stdio(shim, instream, outstream, transport_context):
    """JSON-lines loop for hosting the shim behind a transport.

    ``transport_context`` is fixed by the hosting process before the loop
    starts (e.g. derived from the SSH connection or Tailscale peer). Request
    lines may contain only ``method`` and ``params``; a line that carries a
    ``context`` key is rejected, so a remote caller cannot inject identity.
    Returns the number of handled lines. Offline use and tests only; any
    network hosting still needs the Issue #1 security review.
    """
    handled = 0
    for line in instream:
        line = line.strip()
        if not line:
            continue
        handled += 1
        try:
            request = json.loads(line)
        except (ValueError, TypeError):
            response = {"ok": False, "reason": "invalid_json"}
        else:
            if not isinstance(request, dict) or "context" in request:
                response = {"ok": False, "reason": "context_injection"}
            else:
                response = shim.handle(
                    transport_context,
                    request.get("method"),
                    request.get("params") or {},
                )
        outstream.write(json.dumps(response, ensure_ascii=False) + "\n")
        outstream.flush()
    return handled
