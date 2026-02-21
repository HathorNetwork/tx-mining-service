# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Background block miner for dev/test environments.

Replaces cpuminer by polling the fullnode for block templates, solving the trivial
PoW in-process, and submitting the solved block back to the fullnode.
"""

import asyncio
from typing import TYPE_CHECKING, Optional

from hathorlib import Block
from hathorlib.exceptions import PushTxFailed
from structlog import get_logger

import txstratum.time

if TYPE_CHECKING:
    from hathorlib.client import HathorClient

logger = get_logger()


def solve_block(block: Block) -> bool:
    """Solve PoW for a block by iterating nonces.

    With weight ~21 (minimum), roughly 1 in 2M nonces is valid. This is
    solvable in milliseconds on modern hardware.

    Returns True if a valid nonce was found, False otherwise.
    """
    for nonce in range(2**24):
        block.nonce = nonce
        block.update_hash()
        if block.verify_pow():
            return True
    return False


class BlockMiner:
    """Background block miner that polls fullnode for templates and mines them.

    This replaces the cpuminer + stratum connection for dev/test environments.
    """

    def __init__(
        self,
        backend: "HathorClient",
        address: Optional[str] = None,
        block_interval_ms: int = 1000,
    ):
        self.log = logger.new()
        self.backend = backend
        self.address = address
        self.block_interval_s = block_interval_ms / 1000.0
        self.blocks_found = 0
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the block mining loop."""
        self._running = True
        self._task = asyncio.ensure_future(self._run())
        self.log.info(
            "Block miner started",
            address=self.address,
            block_interval_ms=int(self.block_interval_s * 1000),
        )

    async def stop(self) -> None:
        """Stop the block mining loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.log.info("Block miner stopped", blocks_found=self.blocks_found)

    async def _wait_for_fullnode(self) -> None:
        """Wait until the fullnode is ready to serve block templates."""
        while self._running:
            try:
                await self.backend.get_block_template(address=self.address)
                self.log.info("Fullnode is ready")
                return
            except Exception:
                self.log.info("Waiting for fullnode to become available...")
            await asyncio.sleep(1)

    async def _run(self) -> None:
        """Main mining loop."""
        await self._wait_for_fullnode()

        while self._running:
            try:
                await self._mine_one_block()
            except asyncio.CancelledError:
                return
            except Exception:
                self.log.exception("Error in block mining loop")
                await asyncio.sleep(1)
                continue

            await asyncio.sleep(self.block_interval_s)

    async def _mine_one_block(self) -> None:
        """Fetch a block template, solve it, and submit it."""
        start = txstratum.time.time()

        template = await self.backend.get_block_template(address=self.address)
        block = Block.create_from_struct(template.data)

        if not solve_block(block):
            self.log.error("Failed to solve block", height=template.height)
            return

        elapsed_ms = (txstratum.time.time() - start) * 1000

        try:
            await self.backend.push_tx_or_block(bytes(block))
        except PushTxFailed:
            self.log.error(
                "Block rejected by fullnode",
                height=template.height,
                hash=block.hash_hex,
            )
            return

        self.blocks_found += 1
        self.log.info(
            "Block mined",
            height=template.height,
            hash=block.hash_hex,
            weight=block.weight,
            nonce=block.nonce,
            time_ms=f"{elapsed_ms:.1f}",
        )
