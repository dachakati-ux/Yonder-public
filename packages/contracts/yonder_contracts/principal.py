"""Normalized request principal supplied by upstream corporate security."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True)
class RequestPrincipal:
    """Trusted identity and authorization context.

    The rest of Yonder must depend only on this object, not on a particular
    corporate header, token, proxy, or identity library.
    """

    subject: str
    principal_type: str
    roles: tuple[str, ...] = field(default_factory=tuple)
    groups: tuple[str, ...] = field(default_factory=tuple)
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    attributes: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    upstream_request_id: str | None = None
    authentication_context_id: str | None = None

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities
