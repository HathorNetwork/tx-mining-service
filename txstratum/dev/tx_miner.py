# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Synchronous transaction miner for dev/test environments.

Replaces the stratum-based mining by solving trivial PoW in-process.
"""

from hathorlib.base_transaction import BaseTransaction
from structlog import get_logger

logger = get_logger()

# Full 32-bit nonce space. At weight 22, ~2^22 iterations are needed on
# average, so 2^32 gives a comfortable margin.
MAX_NONCE = 2**32


def solve_tx(tx: BaseTransaction) -> bool:
    """Solve PoW for a transaction by iterating nonces.

    Returns True if a valid nonce was found.
    """
    for nonce in range(MAX_NONCE):
        tx.nonce = nonce
        tx.update_hash()
        if tx.verify_pow():
            return True
    return False
