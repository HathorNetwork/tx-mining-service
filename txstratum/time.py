# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import time as _time
from typing import Callable, Optional


def time_system_clock() -> float:
    """Return system clock timestamp."""
    return _time.time()


time: Callable[[], float] = time_system_clock


def set_time_function(fn: Optional[Callable[[], float]]) -> None:
    """Set a new function for time."""
    global time
    if fn is None:
        time = time_system_clock
    else:
        time = fn
