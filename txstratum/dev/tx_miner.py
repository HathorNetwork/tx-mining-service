# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Synchronous transaction miner for dev/test environments.

Replaces the stratum-based mining by solving trivial PoW in-process. With
MIN_TX_WEIGHT=1, ~50% of nonces are valid on the first try.
"""

from hathorlib.base_transaction import BaseTransaction
from structlog import get_logger

logger = get_logger()


def solve_tx(tx: BaseTransaction) -> bool:
    """Solve PoW for a transaction by iterating nonces.

    With weight 1, about 50% of nonces work immediately. Even at weight 21,
    this is solvable in milliseconds.

    Returns True if a valid nonce was found.
    """
    for nonce in range(2**20):
        tx.nonce = nonce
        tx.update_hash()
        if tx.verify_pow():
            return True
    return False
