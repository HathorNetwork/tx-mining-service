import asyncio
from collections import defaultdict
from enum import Enum
from typing import Any, Coroutine, Dict


class TxMiningEvents(Enum):
    MANAGER_TX_SOLVED = 'manager:tx_solved'
    MANAGER_TX_TIMEOUT = 'manager:tx_timeout'
    MANAGER_NEW_TX_JOB = 'manager:new_tx_job'
    PROTOCOL_JOB_SOLVED = 'protocol:job_solved'
    PROTOCOL_JOB_COMPLETED = 'protocol:job_completed'
    

class PubSubManager():
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self._subscribers: Dict[TxMiningEvents, Coroutine] = defaultdict(list)

    def subscribe(self, event: TxMiningEvents, coroutine: Coroutine):
        self._subscribers[event].append(coroutine)

    def emit(self, event: TxMiningEvents, obj: Any):
        for coroutine in self._subscribers[event]:
            coro = coroutine(obj)
            self.loop.create_task(coro)