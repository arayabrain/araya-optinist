import asyncio
import contextvars
from concurrent.futures import Executor
from typing import Callable, TypeVar

_T = TypeVar("_T")


async def run_in_executor_with_context(
    executor: Executor, func: Callable[..., _T], *args
) -> _T:
    """func(*args) on executor, with the caller's context variables, as
    asyncio.to_thread runs it: logs from the thread keep the request's client id."""
    context = contextvars.copy_context()
    return await asyncio.get_running_loop().run_in_executor(
        executor, context.run, func, *args
    )
