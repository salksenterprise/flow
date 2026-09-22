"""Who is asking.

Embedded, this comes from host code that has already authenticated the caller,
so it arrives as a value rather than as parseable text. Flow never reads a
credential, a token or a header: it is handed an identity and trusts the caller
to have established it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Actor:
    actor_id: str
    actor_type: str = "USER"
    organization_id: str | None = None
    roles: frozenset[str] = field(default_factory=frozenset)
    groups: frozenset[str] = field(default_factory=frozenset)
    permissions: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_value(cls, value: Any) -> "Actor":
        """Accepts an Actor, a mapping, a bare string, or nothing.

        The string and mapping forms exist because commands arrive as plain
        data over HTTP and from older callers. Whatever the shape, it becomes
        one type before the engine sees it.
        """
        if isinstance(value, Actor):
            return value
        if value is None:
            return cls(actor_id="system", actor_type="SYSTEM")
        if isinstance(value, str):
            return cls(actor_id=value)
        if isinstance(value, dict):
            return cls(
                actor_id=value.get("actor_id", "system"),
                actor_type=value.get("actor_type", "USER"),
                organization_id=value.get("organization_id"),
                roles=frozenset(value.get("roles") or ()),
                groups=frozenset(value.get("groups") or ()),
                permissions=frozenset(value.get("permissions") or ()),
            )
        raise TypeError(f"Cannot read an actor from {type(value).__name__}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id, "actor_type": self.actor_type,
            "organization_id": self.organization_id,
            "roles": sorted(self.roles), "groups": sorted(self.groups),
            "permissions": sorted(self.permissions),
        }

    def has(self, permission: str) -> bool:
        return permission in self.permissions
