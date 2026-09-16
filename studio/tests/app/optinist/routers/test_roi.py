import pytest

ROI_ACTIONS = [
    ("status", None),
    ("add_roi", {"posx": 1, "posy": 1, "sizex": 1, "sizey": 1}),
    ("merge_roi", {"ids": [0, 1]}),
    ("delete_roi", {"ids": [0]}),
    ("promote_roi", {"ids": [0]}),
    ("commit_edit", None),
    ("cancel_edit", None),
]


def roi_url(workspace_in_path, action, authorized_workspace):
    return (
        f"/api/visualizations/image/{workspace_in_path}/uid/suite2p_roi_x/"
        f"cell_roi.json/{action}?workspace_id={authorized_workspace}"
    )


@pytest.mark.parametrize("action,body", ROI_ACTIONS)
def test_a_path_in_another_workspace_is_refused(client, action, body):
    # The owner of workspace 1 names a node under workspace 2
    res = client.post(roi_url(2, action, 1), json=body)
    assert res.status_code == 403
    assert "workspace" in res.json()["detail"]


def test_a_path_in_the_authorized_workspace_passes_the_binding(client):
    # No node exists there, so the request fails after the binding, not at it
    res = client.post(roi_url(1, "cancel_edit", 1))
    assert res.status_code == 400
