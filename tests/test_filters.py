"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""
from typing import List
from unittest.mock import MagicMock

import asynctest  # type: ignore
from hathorlib.scripts import P2PKH
from hathorlib.transaction import Transaction, TxInput, TxOutput
from hathorlib.utils import decode_address

from txstratum.filters import FileFilter, TOIFilter
from txstratum.toi_client import CheckBlacklist, TOIError


class AsyncMock(MagicMock):  # type: ignore
    """MagicMock for async functions.

    The native unittest.mock.AsyncMock is not being used
    because it was added on python 3.8 and we have to support versions 3.6 and 3.7
    """
    async def __call__(self, *args, **kwargs):
        return super().__call__(*args, **kwargs)


def create_tx_from(inputs: List[TxInput], outputs: List[TxOutput]) -> Transaction:
    tx = Transaction()
    tx.inputs.extend(inputs or [TxInput(tx_id=bytes.fromhex('0000cafe' * 8), index=0, data=bytes.fromhex('cafe'))])
    tx.outputs.extend(
        outputs or [TxOutput(value=1, script=P2PKH.create_output_script(bytes.fromhex('cafecafe01' * 5)))]
    )
    tx.parents.extend([bytes.fromhex("1234abcd" * 8)] * 2)
    tx.update_hash()
    return tx


def create_tx_with(in_txs: List[bytes] = [], out_addresses: List[str] = []) -> Transaction:
    inputs = []
    outputs = []
    for tx_id in in_txs:
        inputs.append(TxInput(tx_id=tx_id, index=0, data=bytes.fromhex('cafe')))
    for address in out_addresses:
        outputs.append(
            TxOutput(
                value=1,
                script=P2PKH.create_output_script(decode_address(address))
            )
        )
    return create_tx_from(inputs, outputs)


class FiltersTestCase(asynctest.ClockedTestCase):  # type: ignore
    async def test_file_filter_address(self):
        fail_address = 'HTQMV7gbUsJeADqTB9tTt6qin5VJcKy6Kb'
        ok_address = 'H9ZVe52vMVbGBXCSEXCS9tZ5YEY3tUk1CL'

        file_filter = FileFilter(banned_addresses=set([fail_address]))

        fail_address_tx = create_tx_with(out_addresses=[fail_address])
        resp_fail_address = await file_filter.check_tx(fail_address_tx, data=None)
        assert resp_fail_address is True

        ok_address_tx = create_tx_with(out_addresses=[ok_address])
        resp_ok_address = await file_filter.check_tx(ok_address_tx, data=None)
        assert resp_ok_address is False

    async def test_file_filter_tx(self):
        fail_tx_id = bytes.fromhex('deadbeef' * 8)
        ok_tx_id = bytes.fromhex('0000dead' * 8)

        file_filter = FileFilter(banned_tx_ids=set([fail_tx_id]))

        fail_id_tx = create_tx_with(in_txs=[fail_tx_id])
        resp_fail_id = await file_filter.check_tx(fail_id_tx, data=None)
        assert resp_fail_id is True

        ok_id_tx = create_tx_with(in_txs=[ok_tx_id])
        resp_ok_id = await file_filter.check_tx(ok_id_tx, data=None)
        assert resp_ok_id is False

    async def test_toi_filter_ok(self):
        ok_resp = CheckBlacklist(blacklisted=False, issues={})
        client = AsyncMock()
        client.check_blacklist = AsyncMock(return_value=ok_resp)
        toi_filter = TOIFilter(client)
        resp = await toi_filter.check_tx(create_tx_from([], []), data=None)
        assert resp is False

    async def test_toi_filter_fail(self):
        fail_resp = CheckBlacklist(blacklisted=True, issues={})
        client = AsyncMock()
        client.check_blacklist = AsyncMock(return_value=fail_resp)
        toi_filter = TOIFilter(client)
        resp = await toi_filter.check_tx(create_tx_from([], []), data=None)
        assert resp is True

    async def test_toi_fail_block(self):
        client = AsyncMock()
        client.check_blacklist = AsyncMock(side_effect=TOIError('error'))
        toi_filter = TOIFilter(client, block=True)
        resp = await toi_filter.check_tx(create_tx_from([], []), data=None)
        assert resp is True

    async def test_toi_fail_pass(self):
        client = AsyncMock()
        client.check_blacklist = AsyncMock(side_effect=TOIError('error'))
        toi_filter = TOIFilter(client, block=False)
        resp = await toi_filter.check_tx(create_tx_from([], []), data=None)
        assert resp is False
