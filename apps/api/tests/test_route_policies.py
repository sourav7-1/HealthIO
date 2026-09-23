"""Rule 2: every route declares an access policy. New routes without one fail here."""

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.core.policies import policy_name
from app.main import create_app


def _policies(dependant: Dependant) -> set[str]:
    found: set[str] = set()
    for dep in dependant.dependencies:
        name = policy_name(dep.call)
        if name:
            found.add(name)
        found |= _policies(dep)
    return found


def test_every_route_has_exactly_one_policy() -> None:
    app = create_app()
    missing: list[str] = []
    multiple: list[tuple[str, list[str]]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        policies = _policies(route.dependant)
        if not policies:
            missing.append(route.path)
        elif len(policies) > 1:
            multiple.append((route.path, sorted(policies)))
    assert not missing, f"routes without a policy: {missing}"
    assert not multiple, f"routes with conflicting policies: {multiple}"
