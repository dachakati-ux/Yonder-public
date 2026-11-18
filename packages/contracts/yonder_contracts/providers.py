"""Provider contract for resolving trusted request context."""

from __future__ import annotations

from typing import Any, Protocol

from yonder_contracts.principal import RequestPrincipal


class RequestContextProvider(Protocol):
    async def resolve(self, request: Any) -> RequestPrincipal:
        """Resolve trusted identity and authorization context.

        This provider does not authenticate users or issue credentials.
        It only normalizes context supplied by an approved upstream
        corporate security system (or a development mock).
        """
        ...
