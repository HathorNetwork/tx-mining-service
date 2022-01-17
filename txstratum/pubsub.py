import asyncio
from collections import defaultdict
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List


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
            self.loop.create_task(coroutine)
