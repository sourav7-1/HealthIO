"""Access policies. Every route must declare exactly one (PROJECT_RULES.md §3).

Phase 1 ships only `public`. Phase 3 adds `require(permission, relationship=...)`,
which authenticates the caller and checks RBAC plus care relationships.
`tests/test_route_policies.py` fails if any route has no policy dependency.
"""

from typing import Any

from fastapi import Depends

POLICY_MARKER = "__hio_policy__"


def _mark(dep: Any, name: str) -> Any:
    setattr(dep, POLICY_MARKER, name)
    return dep


async def _public() -> None:
    """No authentication. Use only for health checks and genuinely public endpoints."""


_mark(_public, "public")

public: Any = Depends(_public)


def policy_name(dependency: Any) -> str | None:
    return getattr(dependency, POLICY_MARKER, None)
