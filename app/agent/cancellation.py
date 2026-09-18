"""Cooperative cancellation support for triage runs.

Running an agent loop is long-lived (reproduce, patch, verify).  Users must be
able to abort a run that is in flight rather than waiting for it to finish.
Because agent commands run synchronously (the executor blocks per tool call),
true pre-emptive interruption of an arbitrary subprocess is not practical.
Instead we use *cooperative* cancellation: a :class:`CancellationToken` is
shared between the process that requests cancellation (e.g. an API handler) and
the running orchestrator.  The orchestrator inspects the token at each stage /
iteration boundary and, when requested, raises :class:`CancelledError` so the
run unwinds cleanly into ``RunStatus.CANCELLED``.

The token is trivially thread/event-loop safe because a triage run is driven by
a single coroutine and cancellation is requested from another coroutine in the
same process.  No locks are required for a plain flag.
"""

from __future__ import annotations


class CancelledError(RuntimeError):
    """Raised inside the orchestrator when a run has been cancelled."""


class CancellationToken:
    """A shared flag a caller flips to request cancellation of a run."""

    def __init__(self) -> None:
        self._cancelled = False
        self._reason: str | None = None

    @property
    def cancelled(self) -> bool:
        """True once cancellation has been requested."""
        return self._cancelled

    @property
    def reason(self) -> str | None:
        """Optional human-readable reason supplied by the canceller."""
        return self._reason

    def cancel(self, reason: str = "cancelled by user") -> None:
        """Request cancellation. Idempotent."""
        if not self._cancelled:
            self._cancelled = True
            self._reason = reason

    def raise_if_cancelled(self) -> None:
        """Raise :class:`CancelledError` if cancellation has been requested."""
        if self._cancelled:
            raise CancelledError(self._reason or "cancelled")
