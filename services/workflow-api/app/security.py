"""Where the HTTP shell gets an actor from.

Embedded, this problem does not arise: the host hands Flow an identity it has
already authenticated. The HTTP shell has no such luxury, so it refuses to
believe the request body and takes identity from a header that a gateway is
expected to inject and that callers cannot set.

With no gateway configured the caller is anonymous and holds no permissions,
so every privileged transition is refused. That is deliberate: the failure mode
of a misconfigured deployment should be no authority, not full authority.
"""

from __future__ import annotations

import json
import os
from typing import Any


ACTOR_HEADER = os.environ.get("WORKFLOW_ACTOR_HEADER", "x-flow-actor")

ANONYMOUS: dict[str, Any] = {
    "actor_id": "anonymous", "actor_type": "USER", "organization_id": None,
    "roles": [], "groups": [], "permissions": [],
}


def actor_from_header(raw: str | None) -> dict[str, Any]:
    """Build an actor from the trusted header. Pure, so it can be tested alone.

    Raises ValueError for a header that is present but unusable, which the
    caller maps to a 400. An absent header is not an error; it means anonymous.
    """
    if not raw:
        return dict(ANONYMOUS)
    try:
        claims = json.loads(raw)
    except ValueError as error:
        raise ValueError(f"{ACTOR_HEADER} is not valid JSON") from error
    if not isinstance(claims, dict) or not claims.get("actor_id"):
        raise ValueError(f"{ACTOR_HEADER} must be an object carrying an actor_id")
    return {
        "actor_id": str(claims["actor_id"]),
        "actor_type": claims.get("actor_type", "USER"),
        "organization_id": claims.get("organization_id"),
        "roles": list(claims.get("roles") or []),
        "groups": list(claims.get("groups") or []),
        "permissions": list(claims.get("permissions") or []),
    }
