# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Synchronous transaction PoW solver for dev/test environments.

In production, transaction PoW is solved by external miners connected via the
stratum protocol. The round-trip (service sends job → miner solves → miner
returns nonce) adds latency and complexity that is unnecessary in a single-node
test setup.

This module replaces that path with a simple brute-force nonce search that runs
in-process. Combined with --test-mode-tx-weight on the fullnode (which sets
transaction weight to ~1), the first nonce almost always passes — making PoW
resolution effectively instant.

The function is designed to be called via `run_in_executor` so it doesn't block
the async event loop (see manager.py).
"""

from hathorlib.base_transaction import BaseTransaction
from structlog import get_logger

logger = get_logger()

# Full 32-bit nonce space. Even at standard weight (~22), the expected number
# of iterations is ~2^22 (~4M), well within the 2^32 (~4B) range. With
# --test-mode-tx-weight (weight ~1), ~50% of nonces are valid.
MAX_NONCE = 2**32


def solve_tx(tx: BaseTransaction) -> bool:
    """Solve PoW for a transaction by iterating nonces.

    This is a blocking function — it should be run in a thread executor
    to avoid blocking the async event loop.

    Returns True if a valid nonce was found.
    """
    for nonce in range(MAX_NONCE):
        tx.nonce = nonce
        tx.update_hash()
        if tx.verify_pow():
            return True
    return False
