import asyncio
import traceback
from collections import defaultdict
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List

from structlog import get_logger

logger = get_logger()


class TxMiningEvents(Enum):
    """Events that can be published to PubSub."""

    MANAGER_TX_SOLVED = 'manager:tx_solved'
    MANAGER_TX_TIMEOUT = 'manager:tx_timeout'
    MANAGER_NEW_TX_JOB = 'manager:new_tx_job'
    PROTOCOL_JOB_COMPLETED = 'protocol:job_completed'


class PubSubManager():
    """Simple pub/sub implementation for asyncio."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        """Initialize PubSubManager.

        :param loop: asyncio event loop
        """
        self.log = logger.new()
        self.loop = loop
        self._subscribers: Dict[TxMiningEvents, List[Callable[[Any], Coroutine[Any, Any, Any]]]] = defaultdict(list)

    def subscribe(self, event: TxMiningEvents, callable: Callable[[Any], Coroutine[Any, Any, Any]]) -> None:
        """Subscribe to an event.

        :param event: event to subscribe to
        :param coroutine: coroutine to run when event is published
        """
        self._subscribers[event].append(callable)

    def emit(self, event: TxMiningEvents, obj: Any) -> None:
        """Emit an event.

        :param event: event to emit
        :param obj: object that will be passed to subscriber coroutines
        """
        for callable in self._subscribers[event]:
            coroutine = callable(obj)
            task = self.loop.create_task(coroutine)
            task.add_done_callback(self.handle_done_task)

    def handle_done_task(self, task: "asyncio.Task[Any]") -> None:
        """Check if there was an exception in a task.

        :param task: task to check
        """
        exception = task.exception()

        if exception:
            self.log.error(
                'Exception when running PubSub subscriber',
                traceback=traceback.format_exception(
                    None, exception, exception.__traceback__
                )
            )
