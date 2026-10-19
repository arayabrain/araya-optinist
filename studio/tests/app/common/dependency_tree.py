"""Walks over a FastAPI route's dependency tree, for tests that guard it.

Include-time router dependencies are part of route.dependant, so a dependency
reached only through APIRouter(dependencies=...) is found too.
"""

REQUEST_PARAM_KINDS = ("query", "header", "cookie", "body")


def depends_on(dependant, target) -> bool:
    """True if target is anywhere below dependant."""
    return any(
        sub.call is target or depends_on(sub, target) for sub in dependant.dependencies
    )


def dependency_request_params(dependant) -> set:
    """(dependency, kind, name) for each request parameter that a dependency,
    not the endpoint itself, declares. Path parameters are left out: they are
    bound to the route's path and cannot appear unnoticed."""
    found = set()
    for sub in dependant.dependencies:
        owner = f"{sub.call.__module__}.{getattr(sub.call, '__qualname__', sub.call)}"
        for kind in REQUEST_PARAM_KINDS:
            for field in getattr(sub, f"{kind}_params"):
                found.add((owner, kind, field.name))
        found |= dependency_request_params(sub)
    return found
