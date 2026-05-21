"""
Tests for INSTANCE_MODE=public router gating.

Verifies that `_register_routers(app, "public")` mounts the public-safe
routers (auth.router, dataview.public_router, internal.router, outputs.router
— auth is on every tier so logins survive a free-tier outage) and skips
the workflow/optinist routers, while default mode registers the full set.
"""

from fastapi import FastAPI

from studio.__main_unit__ import _register_routers


def _registered_paths(instance_mode: str) -> set[str]:
    """Build a fresh FastAPI app, register routers for the mode, return its paths."""
    app = FastAPI()
    _register_routers(app, instance_mode)
    return {route.path for route in app.routes if hasattr(route, "path")}


class TestInstanceModePublic:
    """In INSTANCE_MODE=public, only the public subset is registered."""

    def test_public_router_is_registered(self):
        paths = _registered_paths("public")
        assert any(p.startswith("/api/public/dataview") for p in paths)

    def test_outputs_router_is_registered(self):
        # Public carve-out lives in the auth dependency, not in registration.
        paths = _registered_paths("public")
        assert any(p.startswith("/outputs") for p in paths)

    def test_internal_router_is_registered(self):
        paths = _registered_paths("public")
        assert any(p.startswith("/system-internal") for p in paths)

    def test_auth_router_is_registered_on_public(self):
        # Mounted on public so authentication survives a free-tier outage.
        paths = _registered_paths("public")
        assert any(p.startswith("/auth") for p in paths)

    def test_workflow_router_is_not_registered(self):
        paths = _registered_paths("public")
        assert not any(p.startswith("/workflow") for p in paths)

    def test_run_router_is_not_registered(self):
        paths = _registered_paths("public")
        assert not any(p.startswith("/run") for p in paths)

    def test_authenticated_dataview_router_is_not_registered(self):
        paths = _registered_paths("public")
        non_public_dataview = [
            p
            for p in paths
            if p.startswith("/api/dataview") and not p.startswith("/api/public")
        ]
        assert non_public_dataview == []

    def test_users_me_beacon_router_is_not_registered(self):
        paths = _registered_paths("public")
        assert not any(p.startswith("/users/me") for p in paths)

    def test_subscriptions_router_is_not_registered(self):
        paths = _registered_paths("public")
        assert not any(p.startswith("/subscriptions") for p in paths)

    def test_optinist_routers_are_not_registered(self):
        paths = _registered_paths("public")
        for prefix in ("/hdf5", "/mat", "/nwb"):
            assert not any(
                p.startswith(prefix) for p in paths
            ), f"{prefix} should not be registered in INSTANCE_MODE=public"

    def test_roi_edit_endpoints_are_not_registered(self):
        # roi.router shares prefix "/outputs"; assert each endpoint explicitly.
        paths = _registered_paths("public")
        roi_endpoints = (
            "/outputs/image/{filepath:path}/status",
            "/outputs/image/{filepath:path}/add_roi",
            "/outputs/image/{filepath:path}/merge_roi",
            "/outputs/image/{filepath:path}/delete_roi",
            "/outputs/image/{filepath:path}/commit_edit",
            "/outputs/image/{filepath:path}/cancel_edit",
        )
        for endpoint in roi_endpoints:
            assert endpoint not in paths, (
                f"{endpoint} (roi.router) should not be registered "
                "in INSTANCE_MODE=public"
            )


class TestInstanceModeDefault:
    """In default / non-public mode, all routers are registered (regression check)."""

    def test_workflow_router_is_registered(self):
        paths = _registered_paths("default")
        assert any(p.startswith("/workflow") for p in paths)

    def test_auth_router_is_registered(self):
        paths = _registered_paths("default")
        assert any(p.startswith("/auth") for p in paths)

    def test_public_router_is_still_registered(self):
        paths = _registered_paths("default")
        assert any(p.startswith("/api/public/dataview") for p in paths)

    def test_authenticated_dataview_router_is_registered(self):
        paths = _registered_paths("default")
        non_public = [
            p
            for p in paths
            if p.startswith("/api/dataview") and not p.startswith("/api/public")
        ]
        assert (
            non_public
        ), "authenticated /api/dataview/* should be registered in default mode"
