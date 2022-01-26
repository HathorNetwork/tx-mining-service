"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""

import asyncio
import unittest
from collections import deque
from typing import List, Optional
from unittest.mock import ANY, MagicMock, Mock

import asynctest  # type: ignore
import pytest
from hathorlib.client import BlockTemplate, HathorClient

import txstratum.time
from txstratum.exceptions import JobAlreadyExists
from txstratum.jobs import JobStatus, TxJob
from txstratum.manager import TxMiningManager
from txstratum.protocol import StratumProtocol

TX1_DATA = bytes.fromhex(
    '0001000102000000000000089c0d40a9b1edfb499bc624833fde87ae459d495000393f4aaa00006'
    'a473045022100c407d5e8f411f9ae582ebd7acbfcb6ea6170332709fb69acaa34c1b426f1d8f502'
    '2003847963768eca9bcdf46e758319fb2699fd28ab657d00f54bef46c37a90405e2103755f2920f'
    'f7dc32dc5414cea1cf9e078347f40894caf0c03637d083dbb261c5c000003e800001976a914a04c'
    '9e2a0291f53c618fdad2ecb37748efb0eeeb88ac0000151800001976a914545f1156a3b00df622b'
    '1d92968c21b962e9d7aa588ac4032a8228c4020c35ed18547020000000047c9881d2bf348d5ffd6'
    'ce8398d6bc5d17b3bea75a53c15b7480be950000006ed5794bf69ebe7d7d75e7a0024d98acb85cb'
    '9c101b59b8b6073e8667c84e2ee77'
)
TX1_NONCE = '84e2ee77'

TX2_DATA = bytes.fromhex(
    '00010001020000000000000896f3792cf52e13978baa98ac966639946b558190f52d1d8c4900006a473045'
    '022100cf557f80e59f4cc142dfeff28b54321c1787bc6faddb798093b9bd4e6fa32c60022055fbf4312f08'
    '19748a6480e0d1f7d70276b3cc12276d973f991bdb2f22250b972103755f2920ff7dc32dc5414cea1cf9e0'
    '78347f40894caf0c03637d083dbb261c5c0000170c00001976a914a04c9e2a0291f53c618fdad2ecb37748'
    'efb0eeeb88ac000001f400001976a914a04c9e2a0291f53c618fdad2ecb37748efb0eeeb88ac4032a8228c'
    '4020c35ed184d5020000006ed5794bf69ebe7d7d75e7a0024d98acb85cb9c101b59b8b6073e8667c000001'
    '2a9d7b6a8895fc1fde992187e742eff81ad2e40994b595cc45056d7bb333cf514a'
)
TX2_NONCE = '33cf514a'


TOKEN_CREATION_TX_DATA = bytes.fromhex(
    '00020104000005551d7740fd7d3c0acc50b5677fdd844f1225985aa431e1712af2a2fd'
    '8900006a473045022100a445edb5cd6c79a0a7b5ed837582fd65b8d511ee60b64fd076'
    'e07bd8f63f75a202202dca24320bffc4c3ca2a07cdfff38f7c839bde70ed49ef634ac6'
    '588972836cab2103bfa995d676e3c0ed7b863c74cfef9683fab3163b42b6f21442326a'
    '023fc57fba0000264800001976a9146876f9578221fdb678d4e8376503098a9228b132'
    '88ac00004e2001001976a914031761ef85a24603203c97e75af355b83209f08f88ac00'
    '00000181001976a9149f091256cb98649c7c35df0aad44d7805710691e88ac00000002'
    '81001976a914b1d7a5ee505ad4d3b93ea1a5162ba83d5049ec4e88ac0109546f546865'
    '4d6f6f6e04f09f9a804034a52aec6cece75e0fc0e30200001a72272f48339fcc5d5ec5'
    'deaf197855964b0eb912e8c6eefe00928b6cf600001055641c20b71871ed2c5c7d4096'
    'a34f40888d79c25bce74421646e732dc01ff7369'
)
TOKEN_CREATION_TX_NONCE = '01ff7369'


class HathorClientTest(HathorClient):
    def __init__(self, server_url: str, api_version: str = '/v1a/'):
        self._current_index = 0

        BLOCK_DATA_1 = bytes.fromhex('000001ffffffe8b789180000001976a9147fd4ae0e4fb2d2854e76d359029d8078bb9'
                                     '9649e88ac40350000000000005e0f84a9000000000000000000000000000000278a7e')
        BLOCK_DATA_2 = bytes.fromhex('0000010000190000001976a9143d6dbcbf6e67b2cbcc3225994756a56a5e2d3a2788a'
                                     'c40350000000000005e0f84de03000006cb93385b8b87a545a1cbb6197e6caff600c1'
                                     '2cc12fc54250d39c8088fc0002d4d2a15def7604688e1878ab681142a7b155cbe52a6'
                                     'b4e031250ae96db0a0002ad8d1519daaddc8e1a37b14aac0b045129c01832281fb1c0'
                                     '2d873c7abbf9623731383164323332613136626139353030316465323264333135316'
                                     '2303237652d3833623135646233343639373438626262396262393330363861383633'
                                     '3634362d6365326637376239393130373434613162313665656666306630323161306'
                                     '63200000002000000000000000080326758')
        self._block_templates = [
            BlockTemplate(data=BLOCK_DATA_1, height=0),
            BlockTemplate(data=BLOCK_DATA_2, height=1),
        ]

    def next_block_template(self) -> None:
        self._current_index += 1

    async def start(self):
        pass

    async def stop(self):
        pass

    async def get_block_template(self, address: Optional[str] = None) -> BlockTemplate:
        return self._block_templates[self._current_index]

    async def get_tx_parents(self) -> List[bytes]:
        pass

    async def push_tx_or_block(self, raw: bytes) -> bool:
        self.next_block_template()
        return True


def _get_ready_miner(manager: TxMiningManager, address: Optional[str] = None) -> StratumProtocol:
    conn = StratumProtocol(manager)
    conn._update_job_timestamp = False

    transport = Mock()
    conn.connection_made(transport=transport)

    if address:
        params = {'address': address}
    else:
        params = {}
    conn.method_subscribe(params=params, msgid=None)
    conn.method_authorize(params=None, msgid=None)
    return conn


class ManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        address = 'HC7w4j7mPet49BBN5a2An3XUiPvK6C1TL7'

        self.client = HathorClientTest(server_url='')
        self.loop.run_until_complete(self.client.start())
        self.manager = TxMiningManager(backend=self.client, pubsub=MagicMock(), address=address)
        self.loop.run_until_complete(self.manager.start())
        self.loop.run_until_complete(self.manager.wait_for_block_template())
        self.assertTrue(len(self.manager.block_template) > 0)

    def _run_all_pending_events(self):
        """Run all pending events."""
        # pending = asyncio.all_tasks(self.loop)
        # self.loop.run_until_complete(asyncio.gather(*pending))
        async def _fn():
            pass
        future = asyncio.ensure_future(_fn())
        self.loop.run_until_complete(future)

    def test_invalid_mining_address(self):
        from hathorlib.exceptions import InvalidAddress
        address = 'HC7w4j7mPet49BBN5a2An3XUiPvK6C1TL7'

        invalid_addresses = [
            ('Invalid base58', address[:-1] + 'I'),  # No 'I' in base58 symbols.
            ('Invalid checksum', address[:-1] + 'A'),
            ('Invalid size (smaller)', address[:-1]),
            ('Invalid size (bigger)', address + '7'),
        ]
        for idx, (cause, invalid_address) in enumerate(invalid_addresses):
            with self.assertRaises(InvalidAddress):
                print('Address #{}: {} ({})'.format(idx, cause, invalid_address))
                TxMiningManager(backend=self.client, pubsub=None, address=invalid_address)

    def test_miner_connect_disconnect(self):
        conn = StratumProtocol(self.manager)
        conn.connection_made(transport=None)
        self.assertEqual(1, len(self.manager.connections))
        self.assertEqual(0, len(self.manager.miners))
        conn.connection_lost(exc=None)
        self.assertEqual(0, len(self.manager.connections))
        self.assertEqual(0, len(self.manager.miners))

    def test_miner_connect_ready_disconnect(self):
        conn = StratumProtocol(self.manager)
        transport = Mock()
        conn.connection_made(transport=transport)
        self.assertEqual(1, len(self.manager.connections))
        self.assertEqual(0, len(self.manager.miners))

        conn.method_subscribe(params=None, msgid=None)
        conn.method_authorize(params=None, msgid=None)
        self.assertEqual(1, len(self.manager.miners))

        conn.connection_lost(exc=None)
        self.assertEqual(0, len(self.manager.connections))
        self.assertEqual(0, len(self.manager.miners))

    def test_many_miners_connect_ready_disconnect(self, qty=5):
        transport = Mock()
        connections = []
        for idx in range(qty):
            conn = StratumProtocol(self.manager)
            conn.connection_made(transport=transport)
            self.assertEqual(idx + 1, len(self.manager.connections))
            self.assertEqual(0, len(self.manager.miners))
            connections.append(conn)

        self.assertEqual(qty, len(self.manager.connections))
        self.assertEqual(0, len(self.manager.miners))

        for idx, conn in enumerate(connections):
            conn.method_subscribe(params=None, msgid=None)
            conn.method_authorize(params=None, msgid=None)
            self.assertEqual(idx + 1, len(self.manager.miners))

        self.assertEqual(qty, len(self.manager.connections))
        self.assertEqual(qty, len(self.manager.miners))
        self.manager.status()

        for idx, conn in enumerate(connections):
            conn.connection_lost(exc=None)
            self.assertEqual(qty - idx - 1, len(self.manager.connections))
            self.assertEqual(qty - idx - 1, len(self.manager.miners))

        self.assertEqual(0, len(self.manager.connections))
        self.assertEqual(0, len(self.manager.miners))

    def test_miner_some_jsonrpc_methods(self):
        conn = StratumProtocol(self.manager)
        conn.connection_made(transport=None)

        conn.send_result = MagicMock(return_value=None)
        conn.method_extranonce_subscribe(params=None, msgid=None)
        conn.send_result.assert_called_with(None, True)

        conn.send_result = MagicMock(return_value=None)
        conn.method_multi_version(params=None, msgid=None)
        conn.send_result.assert_called_with(None, True)

    def test_miner_method_subscribe_invalid_address1(self):
        conn = StratumProtocol(self.manager)
        transport = Mock()
        conn.connection_made(transport=transport)
        conn.send_error = MagicMock(return_value=None)

        params = {
            'address': 'abc!'
        }
        conn.method_subscribe(params=params, msgid=None)
        conn.send_error.assert_called_once()
        transport.close.assert_called_once()

    def test_miner_method_subscribe_invalid_address2(self):
        conn = StratumProtocol(self.manager)
        transport = Mock()
        conn.connection_made(transport=transport)
        conn.send_error = MagicMock(return_value=None)

        params = {
            'address': 'ZiCa'
        }
        conn.method_subscribe(params=params, msgid=None)
        conn.send_error.assert_called_once()
        transport.close.assert_called_once()

    def test_miner_method_subscribe_invalid_address3(self):
        conn = StratumProtocol(self.manager)
        transport = Mock()
        conn.connection_made(transport=transport)
        conn.send_error = MagicMock(return_value=None)

        params = {
            'address': 'HVZjvL1FJ23kH3buGNuttVRsRKq66WHXXX'
        }
        conn.method_subscribe(params=params, msgid=None)
        conn.send_error.assert_called_once()
        transport.close.assert_called_once()

    def test_miner_method_subscribe_valid_address(self):
        conn = StratumProtocol(self.manager)
        transport = Mock()
        conn.connection_made(transport=transport)
        conn.send_error = MagicMock(return_value=None)

        params = {
            'address': 'HVZjvL1FJ23kH3buGNuttVRsRKq66WHUVZ'
        }
        conn.method_subscribe(params=params, msgid=None)
        conn.send_error.assert_not_called()
        transport.close.assert_not_called()

    def _get_ready_miner(self, address: Optional[str] = None) -> StratumProtocol:
        return _get_ready_miner(self.manager, address)

    def test_miner_invalid_address(self):
        conn = StratumProtocol(self.manager)
        conn.send_error = MagicMock(return_value=None)

        transport = Mock()
        conn.connection_made(transport=transport)

        params = {'address': 'X'}
        conn.method_subscribe(params=params, msgid=None)
        conn.send_error.assert_called_once_with(None, conn.INVALID_ADDRESS)

    def test_miner_only_blocks_submit_failed_1(self):
        conn = self._get_ready_miner('HVZjvL1FJ23kH3buGNuttVRsRKq66WHUVZ')
        self.assertIsNotNone(conn.current_job)
        self.assertTrue(conn.current_job.is_block)
        conn.send_error = MagicMock(return_value=None)
        conn.method_submit(params={}, msgid=None)
        conn.send_error.assert_called_once_with(None, conn.INVALID_PARAMS, ANY)

    def test_miner_only_blocks_submit_failed_2(self):
        conn = self._get_ready_miner('HVZjvL1FJ23kH3buGNuttVRsRKq66WHUVZ')
        self.assertIsNotNone(conn.current_job)
        self.assertTrue(conn.current_job.is_block)

        params = {
            'job_id': 'abc!',
            'nonce': '123',
        }
        conn.send_error = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_called_once_with(None, conn.INVALID_PARAMS, ANY)

    def test_miner_only_blocks_submit_failed_3(self):
        conn = self._get_ready_miner('HVZjvL1FJ23kH3buGNuttVRsRKq66WHUVZ')
        self.assertIsNotNone(conn.current_job)
        self.assertTrue(conn.current_job.is_block)

        params = {
            'job_id': 'ffff',
            'nonce': '123',
        }
        conn.send_error = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_called_once_with(None, conn.JOB_NOT_FOUND)

    def test_miner_only_blocks_submit_failed_4(self):
        conn = self._get_ready_miner('HVZjvL1FJ23kH3buGNuttVRsRKq66WHUVZ')
        self.assertIsNotNone(conn.current_job)
        self.assertTrue(conn.current_job.is_block)

        params = {
            'job_id': conn.current_job.uuid.hex(),
            'nonce': 'FFZZ',
        }
        conn.send_error = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_called_once_with(None, conn.INVALID_PARAMS, ANY)

    def test_miner_only_blocks_submit_failed_5(self):
        conn = self._get_ready_miner('HVZjvL1FJ23kH3buGNuttVRsRKq66WHUVZ')
        self.assertIsNotNone(conn.current_job)
        self.assertTrue(conn.current_job.is_block)

        params = {
            'job_id': conn.current_job.uuid.hex(),
            'nonce': '123',
        }
        conn.send_error = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_called_once_with(None, conn.INVALID_SOLUTION)

    def test_miner_only_blocks_submit(self):
        conn = self._get_ready_miner()
        self.assertIsNotNone(conn.current_job)
        self.assertTrue(conn.current_job.is_block)
        self.assertEqual(0, conn.current_job.height)

        # First submission: success
        params = {
            'job_id': conn.current_job.uuid.hex(),
            'nonce': '00000000000000000000000000278a7e',
        }
        conn.send_error = MagicMock(return_value=None)
        conn.send_result = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_not_called()
        conn.send_result.assert_called_once_with(None, 'ok')

        # Second submission: stale job
        conn.send_error = MagicMock(return_value=None)
        conn.send_result = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_called_once_with(None, conn.STALE_JOB, ANY)
        conn.send_result.assert_not_called()

        self._run_all_pending_events()
        self.loop.run_until_complete(self.manager.update_block_template())
        self.assertEqual(1, conn.current_job.height)

        # conn.connection_lost(exc=None)
        # self.loop.run_until_complete(self.manager.stop())

    def test_miner_only_blocks_update_block(self):
        conn = self._get_ready_miner()
        self.assertIsNotNone(conn.current_job)
        self.assertTrue(conn.current_job.is_block)
        self.assertEqual(0, conn.current_job.height)

        # Hathor full node returned a new block template.
        self.client.next_block_template()
        self.loop.run_until_complete(self.manager.update_block_template())
        self._run_all_pending_events()

        self.assertEqual(1, conn.current_job.height)

    def test_two_miners_same_submission_1(self):
        conn1 = self._get_ready_miner()
        conn2 = self._get_ready_miner()
        self.assertEqual(0, conn1.current_job.height)
        self.assertEqual(0, conn2.current_job.height)

        # First submission: success
        params = {
            'job_id': conn1.current_job.uuid.hex(),
            'nonce': '00000000000000000000000000278a7e',
        }
        conn1.send_error = MagicMock(return_value=None)
        conn1.send_result = MagicMock(return_value=None)
        self.manager.backend.push_tx_or_block = MagicMock(return_value=asyncio.Future())
        conn1.method_submit(params=params, msgid=None)
        conn1.send_error.assert_not_called()
        conn1.send_result.assert_called_once_with(None, 'ok')
        self.manager.backend.push_tx_or_block.assert_called_once()

        # As the main loop is not running, the jobs have not been updated yet.
        # Second submission: success, but it won't be propagated.
        conn2.send_error = MagicMock(return_value=None)
        conn2.send_result = MagicMock(return_value=None)
        self.manager.backend.push_tx_or_block = MagicMock(return_value=asyncio.Future())
        conn2.method_submit(params=params, msgid=None)
        conn1.send_error.assert_not_called()
        conn1.send_result.assert_called_once_with(None, 'ok')
        self.manager.backend.push_tx_or_block.assert_not_called()

    def test_two_miners_same_submission_2(self):
        conn1 = self._get_ready_miner()
        conn2 = self._get_ready_miner()
        self.assertEqual(0, conn1.current_job.height)
        self.assertEqual(0, conn2.current_job.height)
        params1 = {
            'job_id': conn1.current_job.uuid.hex(),
            'nonce': '00000000000000000000000000278a7e',
        }
        params2 = {
            'job_id': conn2.current_job.uuid.hex(),
            'nonce': '00000000000000000000000000278a7e',
        }

        # First submission: success
        conn1.send_error = MagicMock(return_value=None)
        conn1.send_result = MagicMock(return_value=None)
        conn1.method_submit(params=params1, msgid=None)
        conn1.send_error.assert_not_called()
        conn1.send_result.assert_called_once_with(None, 'ok')

        # Run the main loop to update the jobs.
        self._run_all_pending_events()
        self.loop.run_until_complete(self.manager.update_block_template())
        self.assertEqual(1, conn1.current_job.height)
        self.assertEqual(1, conn2.current_job.height)

        # As jobs have been updated, the submission from the second miner will be accepted but not propagated.
        # Second submission: success and not propagated.
        conn2.send_error = MagicMock(return_value=None)
        conn2.send_result = MagicMock(return_value=None)
        self.manager.backend.push_tx_or_block = MagicMock(return_value=asyncio.Future())
        conn2.method_submit(params=params2, msgid=None)
        conn1.send_error.assert_not_called()
        conn1.send_result.assert_called_once_with(None, 'ok')
        self.manager.backend.push_tx_or_block.assert_not_called()

    def _run_basic_tx_tests(self, conn, tx_data, tx_nonce):
        job = TxJob(tx_data)
        ret = self.manager.add_job(job)
        self.assertFalse(conn.current_job.is_block)
        self.assertEqual(conn.current_job.tx_job, job)
        self.assertTrue(ret)

        # First submission: wrong nonce
        params = {
            'job_id': conn.current_job.uuid.hex(),
            'nonce': '84e20000',
        }
        conn.send_error = MagicMock(return_value=None)
        conn.send_result = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_called_once_with(None, conn.INVALID_SOLUTION)
        conn.send_result.assert_not_called()
        self.assertFalse(conn.current_job.is_block)

        # Second submission: success
        params = {
            'job_id': conn.current_job.uuid.hex(),
            'nonce': tx_nonce,
        }
        conn.send_error = MagicMock(return_value=None)
        conn.send_result = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_not_called()
        conn.send_result.assert_called_once_with(None, 'ok')

        # Third submission: stale
        conn.send_error = MagicMock(return_value=None)
        conn.send_result = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_called_once_with(None, conn.STALE_JOB, ANY)
        conn.send_result.assert_not_called()

    def test_one_miner_one_tx(self):
        conn = self._get_ready_miner('HVZjvL1FJ23kH3buGNuttVRsRKq66WHUVZ')
        self.assertIsNotNone(conn.current_job)
        self.assertTrue(conn.current_job.is_block)
        self.assertEqual(0, conn.current_job.height)

        self._run_basic_tx_tests(conn, TX1_DATA, TX1_NONCE)

        # Run loop and check that the miner gets a block
        self._run_all_pending_events()
        self.assertTrue(conn.current_job.is_block)
        self.assertEqual(0, conn.current_job.height)

    def test_one_miner_two_txs(self):
        conn = self._get_ready_miner('HVZjvL1FJ23kH3buGNuttVRsRKq66WHUVZ')
        self.assertIsNotNone(conn.current_job)
        self.assertTrue(conn.current_job.is_block)
        self.assertEqual(0, conn.current_job.height)

        job1 = TxJob(TX1_DATA)
        job2 = TxJob(TX2_DATA)
        ret1 = self.manager.add_job(job1)
        ret2 = self.manager.add_job(job2)
        self.assertFalse(conn.current_job.is_block)
        self.assertEqual(conn.current_job.tx_job, job1)
        self.assertTrue(ret1)
        self.assertTrue(ret2)

        # First submission: success
        params = {
            'job_id': conn.current_job.uuid.hex(),
            'nonce': TX1_NONCE,
        }
        conn.send_error = MagicMock(return_value=None)
        conn.send_result = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_not_called()
        conn.send_result.assert_called_once_with(None, 'ok')

        # Run loop and check that the miner gets the next tx
        self._run_all_pending_events()
        self.assertFalse(conn.current_job.is_block)
        self.assertEqual(conn.current_job.tx_job, job2)

        # First submission: success
        params = {
            'job_id': conn.current_job.uuid.hex(),
            'nonce': TX2_NONCE,
        }
        conn.send_error = MagicMock(return_value=None)
        conn.send_result = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_not_called()
        conn.send_result.assert_called_once_with(None, 'ok')

        # Run loop and check that the miner gets a block
        self._run_all_pending_events()
        self.assertTrue(conn.current_job.is_block)
        self.assertEqual(0, conn.current_job.height)

    def test_two_miners_same_tx_submission(self):
        # Motivated by the exceptions described in https://github.com/HathorNetwork/tx-mining-service/pull/38
        conn = self._get_ready_miner()
        self.assertIsNotNone(conn.current_job)
        self.assertTrue(conn.current_job.is_block)
        self.assertEqual(0, conn.current_job.height)

        conn2 = self._get_ready_miner()
        self.assertIsNotNone(conn2.current_job)
        self.assertTrue(conn2.current_job.is_block)
        self.assertEqual(0, conn2.current_job.height)

        job1 = TxJob(TX1_DATA)
        job2 = TxJob(TX2_DATA)
        ret1 = self.manager.add_job(job1)
        ret2 = self.manager.add_job(job2)
        self.assertFalse(conn.current_job.is_block)
        self.assertEqual(conn.current_job.tx_job, job1)
        self.assertTrue(ret1)
        self.assertTrue(ret2)

        old_current_job = conn2.current_job

        # First submission: success
        params = {
            'job_id': conn.current_job.uuid.hex(),
            'nonce': TX1_NONCE,
        }
        conn.send_error = MagicMock(return_value=None)
        conn.send_result = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_not_called()
        conn.send_result.assert_called_once_with(None, 'ok')

        self.assertFalse(conn2.current_job.is_block)

        # This simulates a submission on the old job, which is the same the other miner
        # submitted. This is something we observed happening in the wild.
        conn2.current_job = old_current_job
        self.assertEqual(conn2.current_job.tx_job, job1)
        params = {
            'job_id': conn2.current_job.uuid.hex(),
            'nonce': TX1_NONCE,
        }
        conn2.send_error = MagicMock(return_value=None)
        conn2.send_result = MagicMock(return_value=None)
        conn2.method_submit(params=params, msgid=None)
        conn2.send_error.assert_not_called()
        conn2.send_result.assert_called_once_with(None, 'ok')

        # Run loop and check that the miner gets the next tx
        self._run_all_pending_events()
        self.assertFalse(conn.current_job.is_block)
        self.assertEqual(conn.current_job.tx_job, job2)

        # Second submission: success
        params = {
            'job_id': conn.current_job.uuid.hex(),
            'nonce': TX2_NONCE,
        }
        conn.send_error = MagicMock(return_value=None)
        conn.send_result = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_not_called()
        conn.send_result.assert_called_once_with(None, 'ok')

        # Run loop and check that the miners get a block
        self._run_all_pending_events()
        self.assertTrue(conn.current_job.is_block)
        self.assertEqual(0, conn.current_job.height)
        self.assertTrue(conn2.current_job.is_block)
        self.assertEqual(0, conn2.current_job.height)

    def test_mining_tx_connection_lost(self):
        conn1 = self._get_ready_miner('HVZjvL1FJ23kH3buGNuttVRsRKq66WHUVZ')
        self.assertIsNotNone(conn1.current_job)
        self.assertTrue(conn1.current_job.is_block)
        self.assertEqual(0, conn1.current_job.height)

        conn2 = self._get_ready_miner('HVZjvL1FJ23kH3buGNuttVRsRKq66WHUVZ')
        self.assertIsNotNone(conn2.current_job)
        self.assertTrue(conn2.current_job.is_block)
        self.assertEqual(0, conn2.current_job.height)

        job = TxJob(TX1_DATA)
        ret = self.manager.add_job(job)
        self.assertTrue(ret)
        self.assertFalse(conn1.current_job.is_block)
        self.assertEqual(conn1.current_job.tx_job, job)
        self.assertEqual(conn2.current_job.tx_job, job)

        # Miner 1 disconnects.
        conn1.connection_lost(exc=None)
        self.assertFalse(conn2.current_job.is_block)
        self.assertEqual(conn2.current_job.tx_job, job)

        # Miner 2 disconnects. Tx stays on the queue.
        conn2.connection_lost(exc=None)
        self.assertEqual(deque([job]), self.manager.tx_queue)

        # Miner 3 connects. Tx is sent to the new miner.
        conn3 = self._get_ready_miner('HVZjvL1FJ23kH3buGNuttVRsRKq66WHUVZ')
        self.assertFalse(conn3.current_job.is_block)
        self.assertEqual(conn3.current_job.tx_job, job)

    def test_token_creation_tx(self):
        conn = self._get_ready_miner('HVZjvL1FJ23kH3buGNuttVRsRKq66WHUVZ')
        self.assertIsNotNone(conn.current_job)
        self.assertTrue(conn.current_job.is_block)
        self.assertEqual(0, conn.current_job.height)

        self._run_basic_tx_tests(conn, TOKEN_CREATION_TX_DATA, TOKEN_CREATION_TX_NONCE)

        # Run loop and check that the miner gets a block
        self._run_all_pending_events()
        self.assertTrue(conn.current_job.is_block)
        self.assertEqual(0, conn.current_job.height)

    def test_no_miners_at_start(self):
        from txstratum.constants import DEFAULT_EXPECTED_MINING_TIME

        expected_queue_time = 0

        job1 = TxJob(TX1_DATA)
        self.assertTrue(self.manager.add_job(job1))
        self.assertEqual(DEFAULT_EXPECTED_MINING_TIME, job1.expected_mining_time)
        self.assertEqual(0, job1.expected_queue_time)
        self.assertEqual(1, len(self.manager.tx_queue))

        if DEFAULT_EXPECTED_MINING_TIME > 0:
            expected_queue_time += DEFAULT_EXPECTED_MINING_TIME

        job2 = TxJob(TX2_DATA)
        self.assertTrue(self.manager.add_job(job2))
        self.assertEqual(DEFAULT_EXPECTED_MINING_TIME, job2.expected_mining_time)
        self.assertEqual(expected_queue_time, job2.expected_queue_time)
        self.assertEqual(2, len(self.manager.tx_queue))

        if DEFAULT_EXPECTED_MINING_TIME > 0:
            expected_queue_time += DEFAULT_EXPECTED_MINING_TIME

        job3 = TxJob(TOKEN_CREATION_TX_DATA)
        self.assertTrue(self.manager.add_job(job3))
        self.assertEqual(DEFAULT_EXPECTED_MINING_TIME, job3.expected_mining_time)
        self.assertEqual(expected_queue_time, job3.expected_queue_time)
        self.assertEqual(3, len(self.manager.tx_queue))

        self.assertEqual([job1, job2, job3], list(self.manager.tx_queue))

        # First miner connects and receives job1.
        conn1 = self._get_ready_miner('HVZjvL1FJ23kH3buGNuttVRsRKq66WHUVZ')
        self.assertIsNotNone(conn1.current_job)
        self.assertEqual(job1, conn1.current_job.tx_job)

        # Second miner connects and receives job1.
        conn2 = self._get_ready_miner('HVZjvL1FJ23kH3buGNuttVRsRKq66WHUVZ')
        self.assertIsNotNone(conn2.current_job)
        self.assertEqual(job1, conn2.current_job.tx_job)

    def test_refresh_tx_job(self):
        """Test running the refresh job task when the miner is mining a TxJob.
        The miner shouldn't be told to replace the TxJob by itself with the clean=True param.
        For a different TxJob, the miner should be told to replace the TxJob.
        """
        conn = self._get_ready_miner()
        self.assertIsNotNone(conn.current_job)
        self.assertTrue(conn.current_job.is_block)
        self.assertEqual(0, conn.current_job.height)

        job1 = TxJob(TX1_DATA)
        ret1 = self.manager.add_job(job1)
        self.assertFalse(conn.current_job.is_block)
        self.assertEqual(conn.current_job.tx_job, job1)
        self.assertTrue(ret1)

        conn.send_request = MagicMock(return_value=None)
        conn.send_request.assert_not_called()

        current_job = conn.current_job

        # Run the refresh job task
        self.loop.run_until_complete(conn.refresh_job())
        self._run_all_pending_events()

        # Make sure all jobs sent to the miner had clean=False
        # and that they were all the same txJob
        for c in conn.send_request.mock_calls:
            args = c[1]
            job_data = args[1]
            self.assertEqual(job_data['clean'], False)
            self.assertEqual(job_data['data'], current_job.get_header_without_nonce().hex())

        conn.send_request.reset_mock()

        # Add a new job
        job2 = TxJob(TX2_DATA)
        ret2 = self.manager.add_job(job2)
        self.assertFalse(conn.current_job.is_block)
        self.assertNotEqual(conn.current_job.tx_job, job2)
        self.assertTrue(ret2)

        # Shouldn't send a new job to the miner, since it's already mining a tx
        conn.send_request.assert_not_called()

        # Submit the current job
        params = {
            'job_id': conn.current_job.uuid.hex(),
            'nonce': TX1_NONCE,
        }
        conn.send_error = MagicMock(return_value=None)
        conn.send_result = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_not_called()
        conn.send_result.assert_called_once_with(None, 'ok')

        self.assertIs(conn.current_job.tx_job, job2)

        # Should have sent a new txJob to the miner, with clean=True
        conn.send_request.assert_called_once()
        job_data = conn.send_request.call_args[0][1]

        self.assertEqual(job_data['clean'], True)
        self.assertEqual(job_data['data'], conn.current_job.get_header_without_nonce().hex())

    def test_miner_block_submission_after_receiving_tx(self):
        """
        Simulates a miner that submits a block even after receiving a tx to mine.

        This is something we observed in the wild.
        """
        # Motivated by the exception in https://github.com/HathorNetwork/tx-mining-service/pull/52

        conn = self._get_ready_miner()
        self.assertIsNotNone(conn.current_job)
        self.assertTrue(conn.current_job.is_block)
        self.assertEqual(0, conn.current_job.height)

        block_job = conn.current_job

        # Receive a tx to mine
        job1 = TxJob(TX1_DATA)
        ret1 = self.manager.add_job(job1)
        self.assertFalse(conn.current_job.is_block)
        self.assertEqual(conn.current_job.tx_job, job1)
        self.assertTrue(ret1)

        # Submit a block, even though we just received a tx to mine
        params = {
            'job_id': block_job.uuid.hex(),
            'nonce': '00000000000000000000000000278a7e',
        }

        conn.send_error = MagicMock(return_value=None)
        conn.send_result = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_not_called()
        conn.send_result.assert_called_once_with(None, 'ok')

        # Make sure the block submission was received
        self.assertEqual(len(conn._submitted_work), 1)


class ManagerClockedTestCase(asynctest.ClockedTestCase):  # type: ignore
    def setUp(self):
        address = 'HC7w4j7mPet49BBN5a2An3XUiPvK6C1TL7'

        from tests.utils import Clock
        self.clock = Clock(self.loop)
        self.clock.enable()

        self.client = HathorClientTest(server_url='')
        self.loop.run_until_complete(self.client.start())
        self.manager = TxMiningManager(backend=self.client, pubsub=MagicMock(), address=address)
        self.loop.run_until_complete(self.manager.start())
        self.loop.run_until_complete(self.manager.wait_for_block_template())
        self.assertTrue(len(self.manager.block_template) > 0)

    def tearDown(self):
        self.clock.disable()

    def _get_ready_miner(self, address: Optional[str] = None) -> StratumProtocol:
        return _get_ready_miner(self.manager, address)

    async def test_block_timestamp_update(self):
        job = self.manager.get_best_job(None)
        self.assertTrue(True, job.is_block)

        job.update_timestamp(force=True)
        self.assertEqual(int(txstratum.time.time()), job._block.timestamp)

        # Update timestamp.
        await self.advance(10)
        job.update_timestamp()
        self.assertEqual(int(txstratum.time.time()), job._block.timestamp)

        # Do not update timestamp.
        old_ts = txstratum.time.time()
        await self.advance(40)
        job.update_timestamp()
        self.assertEqual(int(old_ts), job._block.timestamp)

    async def test_tx_resubmit(self):
        job1 = TxJob(TX1_DATA, timeout=10)
        ret1 = self.manager.add_job(job1)
        self.assertTrue(ret1)

        # When a similar job is submitted, manager declines it.
        job2 = TxJob(TX1_DATA)
        with pytest.raises(JobAlreadyExists):
            self.manager.add_job(job2)

        # Wait until job1 is marked as timeout.
        await self.advance(15)
        self.assertEqual(job1.status, JobStatus.TIMEOUT)

        # Try to resubmit a similar job.
        job3 = TxJob(TX1_DATA)
        ret3 = self.manager.add_job(job3)
        self.assertTrue(ret3)

    async def test_tx_timeout_and_cleanup(self):
        job1 = TxJob(TX1_DATA, timeout=10)
        ret1 = self.manager.add_job(job1)
        self.assertTrue(ret1)
        self.assertIn(job1, self.manager.tx_queue)
        self.assertIn(job1.uuid, self.manager.tx_jobs)

        # Wait until job1 is marked as timeout.
        await self.advance(15)
        self.assertEqual(job1.status, JobStatus.TIMEOUT)
        self.assertNotIn(job1, self.manager.tx_queue)
        self.assertIn(job1.uuid, self.manager.tx_jobs)

        # Wait until job1 is cleared.
        await self.advance(self.manager.TX_CLEAN_UP_INTERVAL)
        self.assertNotIn(job1, self.manager.tx_queue)
        self.assertNotIn(job1.uuid, self.manager.tx_jobs)

    async def test_tx_race_condition(self):
        """Test race condition caused when job2 replaces job1 and job1's clean up is close to be executed.
        In this case job1's clean up was cleaning job2 instead.
        """
        job1 = TxJob(TX1_DATA, timeout=10)
        ret1 = self.manager.add_job(job1)
        self.assertTrue(ret1)

        # Wait until job1 is marked as timeout.
        await self.advance(10)
        self.assertEqual(job1.status, JobStatus.TIMEOUT)
        self.assertNotIn(job1, self.manager.tx_queue)
        self.assertIn(job1.uuid, self.manager.tx_jobs)

        # We are 1 second away to cleanup the tx.
        await self.advance(self.manager.TX_CLEAN_UP_INTERVAL - 1)

        # Resubmit a similar job.
        job2 = TxJob(TX1_DATA, timeout=10)
        ret2 = self.manager.add_job(job2)
        self.assertTrue(ret2)
        self.assertIn(job2, self.manager.tx_queue)
        self.assertIn(job2.uuid, self.manager.tx_jobs)

        # Reach the cleanup time of job1.
        await self.advance(2)
        self.assertEqual(job2.status, JobStatus.ENQUEUED)
        self.assertIn(job2, self.manager.tx_queue)
        self.assertIn(job2.uuid, self.manager.tx_jobs)

        # Job2 timeouts.
        await self.advance(15)
        self.assertEqual(job2.status, JobStatus.TIMEOUT)

    async def test_job_mining_time(self):
        conn = self._get_ready_miner()

        job1 = TxJob(TX1_DATA)
        job2 = TxJob(TX2_DATA)
        ret1 = self.manager.add_job(job1)
        ret2 = self.manager.add_job(job2)
        self.assertFalse(conn.current_job.is_block)
        self.assertEqual(conn.current_job.tx_job, job1)
        self.assertTrue(ret1)
        self.assertTrue(ret2)

        await self.advance(10)

        # Submit solution to tx1
        params = {
            'job_id': conn.current_job.uuid.hex(),
            'nonce': TX1_NONCE,
        }
        conn.send_error = MagicMock(return_value=None)
        conn.send_result = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_not_called()
        conn.send_result.assert_called_once_with(None, 'ok')

        await self.advance(10)

        # Submit solution to tx2
        params = {
            'job_id': conn.current_job.uuid.hex(),
            'nonce': TX2_NONCE,
        }
        conn.send_error = MagicMock(return_value=None)
        conn.send_result = MagicMock(return_value=None)
        conn.method_submit(params=params, msgid=None)
        conn.send_error.assert_not_called()
        conn.send_result.assert_called_once_with(None, 'ok')

        # Assertions
        self.assertEqual(job1.get_mining_time(), 10)
        self.assertEqual(job2.get_waiting_time(), 10)
        self.assertEqual(job2.get_mining_time(), 10)
