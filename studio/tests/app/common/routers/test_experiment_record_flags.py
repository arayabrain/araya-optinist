"""The Record list trusts a DB row over the yaml only once finalisation wrote it."""
from datetime import datetime, timezone

import pytest

from studio.app.common.core.experiment.experiment import ExptExtConfig
from studio.app.common.models.experiment import ExperimentRecord
from studio.app.common.routers.experiment import (
    _apply_record_flags,
    _get_experiment_data_flags,
)
from studio.tests.app.common.sqlite_harness import sqlite_session

WORKSPACE_ID = 361


def _config(has_nwb=True):
    return ExptExtConfig(
        workspace_id=str(WORKSPACE_ID),
        unique_id="run",
        name="run",
        started_at="2026-10-05 04:08:00",
        finished_at=None,
        success="success",
        hasNWB=has_nwb,
        function={},
        procs=None,
        nwb={},
        snakemake={},
        data_usage=None,
    )


@pytest.fixture()
def db():
    with sqlite_session([ExperimentRecord.__table__]) as session:
        # The data-usage placeholder written at run start, flags still default
        session.add(ExperimentRecord(workspace_id=WORKSPACE_ID, uid="running"))
        session.add(
            ExperimentRecord(
                workspace_id=WORKSPACE_ID,
                uid="expired",
                success=True,
                analyzed_at=datetime.now(timezone.utc),
                has_nwb=False,
            )
        )
        session.commit()
        yield session


def test_placeholder_row_is_not_finalized_but_completed_row_is(db):
    flags = _get_experiment_data_flags(db, str(WORKSPACE_ID))

    assert flags["running"]["finalized"] is False
    assert flags["running"]["has_nwb"] is False
    assert flags["expired"]["finalized"] is True


def test_non_numeric_workspace_has_no_flags(db):
    assert _get_experiment_data_flags(db, "default") == {}


def test_placeholder_row_leaves_the_yaml_value_alone():
    # The poll that reports FINISHED fetches the list seconds before finalisation
    config = _config(has_nwb=True)

    _apply_record_flags(config, {"finalized": False, "has_nwb": False})

    assert config.hasNWB is True
    assert config.has_outputs is None


def test_finalized_row_overrides_the_yaml():
    config = _config(has_nwb=True)

    _apply_record_flags(
        config,
        {
            "finalized": True,
            "has_nwb": False,
            "has_intermediates": True,
            "has_outputs": False,
            "has_inputs": True,
        },
    )

    assert config.hasNWB is False
    assert (config.has_intermediates, config.has_outputs, config.has_inputs) == (
        True,
        False,
        True,
    )


def test_missing_row_leaves_the_config_alone():
    config = _config(has_nwb=False)

    _apply_record_flags(config, None)

    assert config.hasNWB is False
