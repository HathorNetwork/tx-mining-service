# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional, Set

from hathorlib.scripts import P2PKH
from structlog import get_logger

from txstratum.toi_client import TOIError

if TYPE_CHECKING:
    from hathorlib import BaseTransaction

    from txstratum.toi_client import TOIAsyncClient


logger = get_logger()


class TXFilter(ABC):
    """Base class for tx filters."""

    @abstractmethod
    async def check_tx(self, tx: "BaseTransaction", data: Any) -> bool:
        """Return if the tx should be blocked."""
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """Close any open session."""
        raise NotImplementedError


class FileFilter(TXFilter):
    """Filter tx based on a set of banned tx_ids and addresses."""

    def __init__(
        self, banned_tx_ids: Set[bytes] = set(), banned_addresses: Set[str] = set()
    ) -> None:
        """Init filter."""
        self.log = logger.new()
        self.banned_tx_ids = banned_tx_ids
        self.banned_addresses = banned_addresses

    @classmethod
    def load_from_files(
        cls, tx_filename: Optional[str], address_filename: Optional[str]
    ) -> "FileFilter":
        """Load banned tx and addresses from files and return an instance of FileFilter."""
        file_filter = cls()
        if tx_filename:
            with open(tx_filename, "r") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    file_filter.log.info("Added to banned tx ids", txid=line)
                    file_filter.banned_tx_ids.add(bytes.fromhex(line))

        if address_filename:
            with open(address_filename, "r") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    file_filter.log.info("Added to banned addresses", txid=line)
                    file_filter.banned_addresses.add(line)
        return file_filter

    async def close(self) -> None:
        """Nothing to do here."""
        return

    async def check_tx(self, tx: "BaseTransaction", data: Any) -> bool:
        """Return if the tx should be blocked."""
        if len(self.banned_tx_ids):
            for txin in tx.inputs:
                if txin.tx_id in self.banned_tx_ids:
                    self.log.info("banned-tx", data=data)
                    return True

        if len(self.banned_addresses):
            for txout in tx.outputs:
                p2pkh = P2PKH.parse_script(txout.script)
                if p2pkh is not None:
                    self.log.info("p2pkh.address", address=p2pkh.address)
                    if p2pkh.address in self.banned_addresses:
                        self.log.info("banned-address", data=data)
                        return True
        return False


class TOIFilter(TXFilter):
    """Filter tx based on the toi service."""

    def __init__(self, client: "TOIAsyncClient", block: bool = False) -> None:
        """Init filter."""
        self.client = client
        self.block = block
        self.log = logger.new()

    async def close(self) -> None:
        """Close toi client."""
        await self.client.close()

    async def check_tx(self, tx: "BaseTransaction", data: Any) -> bool:
        """Return if the tx should be blocked."""
        txs: Set[str] = set()
        addrs: Set[str] = set()
        for txin in tx.inputs:
            txs.add(txin.tx_id.hex())

        for txout in tx.outputs:
            p2pkh = P2PKH.parse_script(txout.script)
            if p2pkh is not None:
                self.log.debug("p2pkh.address", address=p2pkh.address)
                addrs.add(p2pkh.address)

        try:
            resp = await self.client.check_blacklist(
                tx_ids=list(txs), addresses=list(addrs)
            )
            if resp.blacklisted:
                self.log.info("banned", data=data, issues=resp.issues)
            return resp.blacklisted
        except TOIError as ex:
            # TOI Service error
            self.log.error("toi_service_error", data=data, body=str(ex))
            if self.block:
                # We are rejecting a tx based on a toi faillure
                # TODO: alert
                self.log.error("toi_error_reject", data=data, alert="[ALERT]")
                return True
            # allow request even if toi fails
            return False
