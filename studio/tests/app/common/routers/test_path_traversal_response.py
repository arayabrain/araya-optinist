"""The half of the path guard that users see: the HTTP status.

join_filepath() raising is only half the design -- the other half is
__main_unit__ mapping InvalidPathError to a 400, which is what lets the
routers stay free of per-parameter validation. That mapping had no coverage,
so a reordered handler or a new middleware could break it silently.

The /run cases are here for a different reason: those handlers wrap their body
in `except Exception` and re-raise as 500, which swallows the mapping unless
InvalidPathError is let past first. Without these, a traversal in the uid is
reported as a server fault rather than a bad request.
"""
import pytest

TRAVERSAL = "%2E%2E/%2E%2E/etc/passwd"


@pytest.mark.parametrize(
    "path",
    [
        f"/api/visualizations/data/{TRAVERSAL}",
        f"/api/visualizations/html/{TRAVERSAL}",
        f"/api/visualizations/inittimedata/{TRAVERSAL}",
    ],
)
def test_traversal_is_refused_with_400(client, path):
    response = client.get(path)
    assert response.status_code == 400


def test_sideways_move_between_workspaces_is_refused(client):
    """Normalises back inside OUTPUT_DIR, so containment alone would allow it."""
    response = client.get("/api/visualizations/html/1/%2E%2E/2/x.html")
    assert response.status_code == 400


def test_response_does_not_echo_the_rejected_path(client):
    """The detail is fixed text -- reflecting the input would hand an attacker
    a way to probe the filesystem layout through the error message."""
    response = client.get(f"/api/visualizations/data/{TRAVERSAL}")
    assert "passwd" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/run/result/1/%2E%2E",
        "/run/cancel/1/%2E%2E",
        "/run/filter/1/%2E%2E/node",
    ],
)
def test_run_handlers_report_a_rejected_path_as_400_not_500(client, path):
    """These wrap everything in `except Exception`; without an explicit
    re-raise the guard's rejection surfaces as a server fault.

    `run` and `run_id` carry the same re-raise for consistency but are not
    listed: with the session fixture's stubbed dependencies they fail on
    `current_user` before any path is built, so a case here would assert on
    the stub rather than on the guard.
    """
    response = client.post(path, json={})
    assert response.status_code == 400
