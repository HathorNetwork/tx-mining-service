# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
import asyncio
import json
import traceback
from asyncio import Future
from collections import OrderedDict, namedtuple
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Generic, Optional, Tuple, TypeVar, Union, cast

from structlog import get_logger

from txstratum.constants import DEFAULT_EXPECTED_MINING_TIME

if TYPE_CHECKING:
    from asyncio.events import AbstractEventLoop

    from hathorlib import BaseTransaction

logger = get_logger()


class Periodic:
    """Create an asyncio task that calls an async function periodically.

    The function is called every `T` seconds, not taking into consideration how long the last call took.
    If the duration of a call is longer than `T` seconds, the function will be called immediately after it finishes.

    Adapted from:
    - https://stackoverflow.com/a/37514633/947511
    - https://stackoverflow.com/a/55505152/947511
    """

    def __init__(self,
                 afunc: Callable[..., Awaitable[None]],
                 interval: Union[int, float],
                 args: Tuple[Any, ...] = (),
                 kwargs: Dict[str, Any] = {}):
        """Create Periodic instance from async function.

        Params:
            interval: number in seconds
        """
        self.afunc = afunc
        self.args = args
        self.kwargs = kwargs
        self.interval = interval
        self.is_started = False
        self._task: Optional[Future[None]] = None

    async def start(self) -> None:
        """Start calling the async function."""
        if not self.is_started:
            self.is_started = True
            # Start task to call func periodically:
            self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        """Stop calling the async function."""
        if self.is_started:
            assert self._task is not None
            self.is_started = False
            # Stop task and await it stopped:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        assert self._task is not None
        while self.is_started:
            try:
                await asyncio.gather(
                    self.afunc(*self.args, **self.kwargs),
                    asyncio.sleep(self.interval),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('periodic call failed')
                break


class LineProtocol(asyncio.Protocol):
    """A protocol that receives only lines.

    Adapted from: https://github.com/twisted/twisted/blob/trunk/src/twisted/protocols/basic.py#L421
    """

    _buffer = b''
    delimiter = b'\n'
    MAX_LENGTH = 16384

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Set things up when a new connection is made."""
        self.transport = cast(asyncio.Transport, transport)

    def data_received(self, data: bytes) -> None:
        """Translate bytes into lines, and calls line_received."""
        lines = (self._buffer + data).split(self.delimiter)
        self._buffer = lines.pop(-1)
        for line in lines:
            if self.transport.is_closing():
                # this is important because the transport may be told to lose
                # the connection by a line within a larger packet, and it is
                # important to disregard all the lines in that packet following
                # the one that told it to close.
                return
            if len(line) > self.MAX_LENGTH:
                self.line_length_exceeded(line)
                return
            else:
                self.line_received(line)
        if len(self._buffer) > self.MAX_LENGTH:
            self.line_length_exceeded(self._buffer)

    def line_received(self, line: bytes) -> None:
        """Process each line received from the client."""
        raise NotImplementedError

    def line_length_exceeded(self, line: bytes) -> None:
        """Handle when the maximum line length is exceeded."""
        self.transport.close()


JSONRPCError = namedtuple('JSONRPCError', 'code message')
JSONRPCId = Optional[Union[int, str]]


class JSONRPCProtocol(LineProtocol):
    """Implementation of the JSONRPC Protocol.

    Specification: https://www.jsonrpc.org/specification
    """

    PARSE_ERROR = JSONRPCError(-32700, 'Parse error')
    INVALID_REQUEST = JSONRPCError(-32600, 'Invalid Request')
    METHOD_NOT_FOUND = JSONRPCError(-32601, 'Method not found')
    INVALID_PARAMS = JSONRPCError(-32602, 'Invalid params')
    INTERNAL_ERROR = JSONRPCError(-32603, 'Internal error')

    # List of available methods by name.
    supported_methods: Dict[str, Callable[[Any, JSONRPCId], None]] = {}

    def line_received(self, line: bytes) -> None:
        """Handle a line received by the client."""
        try:
            data = json.loads(line)
        except ValueError:
            return self.send_error(None, self.PARSE_ERROR, data='cannot decode json')
        if not isinstance(data, dict):
            # This implementation does not support batch.
            return self.send_error(None, self.INVALID_REQUEST, data='json must be an object')
        self.json_received(data)

    def json_received(self, data: Dict[Any, Any]) -> None:
        """Handle a json received by the client."""
        if self.try_as_request(data):
            return
        if self.try_as_response(data):
            return
        return self.send_error(None, self.INVALID_REQUEST, data={
            'message': data,
            'error': 'Could not identify message as request, result or error.'
        })

    def try_as_request(self, data: Dict[Any, Any]) -> bool:
        """Try to parse `data` as a request."""
        if 'method' not in data:
            return False
        method = data['method']  # required
        msgid = data.get('id')
        params = data.get('params')
        self.handle_request(method, params, msgid)
        return True

    def try_as_response(self, data: Dict[Any, Any]) -> bool:
        """Try to parse `data` as a response."""
        if 'result' not in data and 'error' not in data:
            return False
        msgid = data.get('id')
        result = data.get('result')
        error = data.get('error')
        if result is not None and error is not None:
            # Cannot have both result and error at the same time.
            return False
        if result:
            self.handle_result(result, msgid)
            return True
        elif error:
            if 'code' not in error:
                return False
            code = error['code']
            message = error.get('message')
            errdata = error.get('data')
            self.handle_error(code, message, errdata, msgid)
            return True
        return False

    def handle_request(self, method: str, params: Any, msgid: JSONRPCId) -> None:
        """Handle a request from JSONRPC.

        It calls the methods listed on `self.supported_methods`.
        """
        fn = self.supported_methods.get(method)
        if fn is not None:
            fn(params, msgid)
            return

        data = {
            'method': method,
            'supported_methods': list(self.supported_methods.keys())
        }
        self.send_error(msgid, self.METHOD_NOT_FOUND, data=data)

    def handle_result(self, result: Any, msgid: JSONRPCId) -> None:
        """Handle a result from JSONRPC."""
        raise NotImplementedError

    def handle_error(self, code: int, message: str, data: Any, msgid: JSONRPCId) -> None:
        """Handle an error from JSONRPC."""
        raise NotImplementedError

    def send_request(self, method: str, params: Any, msgid: JSONRPCId) -> None:
        """Send a request to JSONRPC."""
        ret = {
            'id': msgid,
            'method': method,
        }
        if params:
            ret['params'] = params
        self.transport.writelines([json.dumps(ret).encode('utf-8'), b'\n'])

    def send_result(self, msgid: JSONRPCId, result: Any) -> None:
        """Send a result to JSONRPC."""
        ret = {
            'id': msgid,
            'result': result,
        }
        self.transport.writelines([json.dumps(ret).encode('utf-8'), b'\n'])

    def send_error(self, msgid: JSONRPCId, error: JSONRPCError, data: Any = None) -> None:
        """Send an error to JSONRPC."""
        ret: Dict[str, Any] = {
            'id': msgid,
            'error': {
                'code': error.code,
                'message': error.message,
            }
        }
        if data:
            ret['error']['data'] = data
        self.transport.writelines([json.dumps(ret).encode('utf-8'), b'\n'])


KT = TypeVar('KT')
VT = TypeVar('VT')


class MaxSizeOrderedDict(OrderedDict, Generic[KT, VT]):  # type: ignore
    """An OrderedDict that has a maximum size, if new elements are added, the oldest elements are silently deleted.

    Kindly stolen from: https://stackoverflow.com/a/49274421/947511

    Examples:
    >>> foo = MaxSizeOrderedDict(max=5)
    >>> foo[1] = 'a'
    >>> foo[2] = 'b'
    >>> foo[3] = 'c'
    >>> foo[4] = 'd'
    >>> foo[5] = 'e'
    >>> foo
    MaxSizeOrderedDict([(1, 'a'), (2, 'b'), (3, 'c'), (4, 'd'), (5, 'e')])
    >>> foo[6] = 'f'
    >>> foo
    MaxSizeOrderedDict([(2, 'b'), (3, 'c'), (4, 'd'), (5, 'e'), (6, 'f')])
    >>> foo[7] = 'g'
    >>> foo
    MaxSizeOrderedDict([(3, 'c'), (4, 'd'), (5, 'e'), (6, 'f'), (7, 'g')])
    """

    def __init__(self, *args: Any, max: int = 0, **kwargs: VT):
        """Init MaxSizeOrderedDict with max number of elements."""
        self._max: int = max
        super().__init__(*args, **kwargs)

    def __setitem__(self, key: KT, value: VT) -> None:
        """Add a new element to the dict."""
        OrderedDict.__setitem__(self, key, value)
        if self._max > 0:
            if len(self) > self._max:
                self.popitem(False)


def calculate_expected_mining_time(miners_hashrate_ghs: float, job_weight: float) -> float:
    """Calculate expected mining time. Return -1 if there is no miner.

    Let K be the number of attempts to find a solution. We know that `K` follows a geometric distribution.
    So, E[K] = 1/p = 2**(job_weight).

    From the CDF, we know that `P(K < k) = 1 - (1 - p)**k`.
    Replacing `mu = log(1 - p)`, we have `P(K < k) = 1 - exp(mu * k)`
    Using the first term of the Taylor series, `log(1 - p) ~= -p`. So, `P(K < k) = 1 - exp(-p * k)`.
    But, from `E[K] = 1/p = 2**(job_weight)`, we know that `p = 2**(-job_weight)`.
    Then, `P(K < k) = 1 - exp(2**(-job_weight) * k)`.

    Let `t` be the mining time, and `H` be the hash rate. Then `k = t * H` and
    `P(T < t) = 1 - exp(-2**(-job_weight) * t * H)`.

    If `t = x * (2**(job_weight)) / H`, then `P(T < t) = 1 - exp(-x)`.
    We know that `H = miners_hashrate_ghs * (2**30)`.

    Finally, `t = x * (2**(job_weight - 30)) / miners_hashrate_ghs

      x  |  P(T < t)
    ----------------
      1  | 0.6321
      2  | 0.8646
      3  | 0.9502 <--- We chose x = 3 because 95% seems good enough.
      4  | 0.9816
      5  | 0.9932
    """
    if miners_hashrate_ghs == 0:
        # What happens when there is no miners or we don't know the total hashrate?
        # We return a default expected mining time. For further information, see the
        # docstring of the constant.
        return DEFAULT_EXPECTED_MINING_TIME
    return 3 * (2**(job_weight - 30)) / miners_hashrate_ghs


def tx_or_block_from_bytes(data: bytes) -> 'BaseTransaction':
    """Create the correct tx subclass from a sequence of bytes."""
    from hathorlib import TxVersion

    # version field takes up the first 2 bytes
    version = int.from_bytes(data[0:2], 'big')

    tx_version = TxVersion(version)
    cls = tx_version.get_cls()
    return cls.create_from_struct(data)


def start_logging(loop: Optional['AbstractEventLoop'] = None) -> None:
    """Initialize logging."""
    if loop is None:
        loop = asyncio.get_event_loop()

    def _exception_handler(loop: 'AbstractEventLoop', context: Dict[str, Any]) -> None:
        """Handle unhandled exceptions in asyncio's main loop."""
        message = context.get('message')
        if not message:
            message = 'Unhandled exception in event loop'

        exc_info: Any
        exception = context.get('exception')
        if exception is not None:
            exc_info = (type(exception), exception, exception.__traceback__)
        else:
            exc_info = False

        extra = {}
        for key in sorted(context):
            if key in {'message', 'exception'}:
                continue
            value = context[key]
            if key == 'source_traceback':
                tb = ''.join(traceback.format_list(value))
                value = 'Object created at (most recent call last):\n'
                value += tb.rstrip()
            elif key == 'handle_traceback':
                tb = ''.join(traceback.format_list(value))
                value = 'Handle created at (most recent call last):\n'
                value += tb.rstrip()
            else:
                value = repr(value)
            extra[key] = value
        logger.exception(message, **extra, exc_info=exc_info)

    loop.set_exception_handler(_exception_handler)
