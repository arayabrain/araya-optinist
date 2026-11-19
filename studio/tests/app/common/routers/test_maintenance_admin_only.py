from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from studio.app.common.core.workflow.workflow import NodeType
from studio.app.common.routers.algolist import get_algolist
from studio.app.common.routers.run import _refuse_maintenance_for_non_admin

ADMIN, MEMBER = SimpleNamespace(is_admin=True), SimpleNamespace(is_admin=False)


def _run_item(node_type, path):
    node = SimpleNamespace(type=node_type, data=SimpleNamespace(path=path))
    return SimpleNamespace(nodeDict={"n": node})


@pytest.mark.parametrize("user", [ADMIN, None])
def test_admins_and_standalone_run_maintenance(user):
    run_item = _run_item(NodeType.ALGO, "maintenance/setup_conda/setup_caiman")
    _refuse_maintenance_for_non_admin(user, run_item)


def test_a_member_cannot_run_maintenance():
    run_item = _run_item(NodeType.ALGO, "maintenance/setup_conda/setup_caiman")
    with pytest.raises(HTTPException) as e:
        _refuse_maintenance_for_non_admin(MEMBER, run_item)
    assert e.value.status_code == 403


@pytest.mark.parametrize(
    "node_type, path",
    [
        (NodeType.ALGO, "caiman/caiman_mc"),
        (NodeType.IMAGE, "maintenance/x.tif"),
        (NodeType.IMAGE, ["maintenance/x.tif"]),
    ],
)
def test_a_member_runs_other_nodes(node_type, path):
    _refuse_maintenance_for_non_admin(MEMBER, _run_item(node_type, path))


@pytest.mark.parametrize("user, shown", [(ADMIN, True), (None, True), (MEMBER, False)])
def test_algolist_shows_maintenance_to_admins_only(user, shown):
    algos = get_algolist(current_user=user)
    assert ("maintenance" in algos) is shown
    assert "caiman" in algos
