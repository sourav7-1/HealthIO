"""Every route declares exactly one access policy (PROJECT_RULES.md §3)."""

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts

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


def _api_routes() -> dict[str, set[str]]:
    """path+method -> policies, using the effective dependant (router-level deps included).

    FastAPI >= 0.140 keeps included routers lazily, so `app.routes` alone does not list
    them; iter_route_contexts() resolves the effective routes.
    """
    app = create_app()
    routes: dict[str, set[str]] = {}
    for ctx in iter_route_contexts(app.routes):
        if not isinstance(ctx.route, APIRoute):
            continue  # docs/openapi routes are not API endpoints
        for method in sorted(ctx.methods or []):
            routes[f"{method} {ctx.path}"] = _policies(ctx.dependant)
    return routes


def test_every_route_has_exactly_one_policy() -> None:
    routes = _api_routes()
    # Guard against this test silently checking nothing again.
    assert len(routes) >= 20, sorted(routes)
    missing = [r for r, p in routes.items() if not p]
    multiple = {r: sorted(p) for r, p in routes.items() if len(p) > 1}
    assert not missing, f"routes without a policy: {missing}"
    assert not multiple, f"routes with conflicting policies: {multiple}"


def test_expected_policies_on_key_routes() -> None:
    routes = _api_routes()
    assert routes["GET /health"] == {"public"}
    assert routes["POST /api/v1/auth/login"] == {"public"}
    assert routes["GET /api/v1/me"] == {"authenticated"}
    assert routes["POST /api/v1/admin/doctors/{doctor_profile_id}/verify"] == {"roles:admin"}
    assert routes["GET /api/v1/doctor/patients"] == {"permission:list_own_patients"}
    assert routes["POST /api/v1/patients/{patient_id}/caregivers"] == {"patient:manage_caregivers"}
    assert routes["GET /api/v1/patients/{patient_id}/access"] == {"patient:any_relationship"}
