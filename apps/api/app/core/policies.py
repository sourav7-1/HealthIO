"""Route policy marker. Every route must declare exactly one policy (PROJECT_RULES.md §3).

`public` lives here. Authenticated policies (authenticated, require_roles,
require_permission, require_patient_permission) live in app/modules/access/dependencies.py
and mark themselves with `mark_policy`. `tests/test_route_policies.py` fails if any route
has no policy dependency, or more than one.
"""

from typing import Any

from fastapi import Depends

POLICY_MARKER = "__hio_policy__"


def mark_policy(dep: Any, name: str) -> Any:
    setattr(dep, POLICY_MARKER, name)
    return dep


async def _public() -> None:
    """No authentication. Use only for health checks and genuinely public endpoints."""


mark_policy(_public, "public")

public: Any = Depends(_public)


def policy_name(dependency: Any) -> str | None:
    return getattr(dependency, POLICY_MARKER, None)
