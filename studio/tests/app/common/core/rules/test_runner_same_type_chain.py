from studio.app.common.core.rules import runner as runner_module
from studio.app.common.core.rules.runner import Runner
from studio.app.optinist.core.nwb.nwb import NWBDATASET


def test_same_type_upstream_node_survives_in_the_merged_nwb(monkeypatch):
    """A -> B of one algorithm share the label key; B must add to it, not replace."""
    monkeypatch.setattr(runner_module, "save_nwb", lambda *args: None)
    upstream = {NWBDATASET.POSTPROCESS: {"covariate_binning_a": {"mean": 1}}}
    nwbfile = {"input": {}, "covariate_binning": upstream}
    own = {NWBDATASET.POSTPROCESS: {"covariate_binning_b": {"mean": 2}}}

    merged = Runner._Runner__save_func_nwb(
        "unused.nwb", "covariate_binning", nwbfile, {"nwbfile": own}
    )

    assert merged["covariate_binning"][NWBDATASET.POSTPROCESS] == {
        "covariate_binning_a": {"mean": 1},
        "covariate_binning_b": {"mean": 2},
    }
