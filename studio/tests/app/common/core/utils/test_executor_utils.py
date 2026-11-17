import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.utils.executor_utils import run_in_executor_with_context


@pytest.mark.asyncio
async def test_the_work_runs_on_the_executor_with_the_callers_context():
    def work(value):
        return threading.current_thread().name, AppLogger.get_client_id(), value

    AppLogger.set_client_id("caller")
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="probe") as executor:
        thread, client_id, value = await run_in_executor_with_context(
            executor, work, 42
        )

    assert thread.startswith("probe")
    assert client_id == "caller"
    assert value == 42


@pytest.mark.asyncio
async def test_an_error_in_the_work_reaches_the_caller():
    def work():
        raise ValueError("from the thread")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(ValueError, match="from the thread"):
            await run_in_executor_with_context(executor, work)
