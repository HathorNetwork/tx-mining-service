"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

import txstratum.time
from txstratum.api import MAX_OUTPUT_SCRIPT_SIZE, MAX_TIMESTAMP_DELTA, MAX_TX_WEIGHT, App
from txstratum.manager import TxMiningManager
from txstratum.utils import tx_or_block_from_bytes

from .tx_examples import INVALID_TX_DATA

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

NFT_CREATION_TX_DATA = bytes.fromhex(
    '00020102000000006effb1f045764bc2cf04f9d3018618d3ea15d7adb4002eb3db33b2d800006b48'
    '30460221008d3725e59935d0f60c63a5b5f865d845252dbdf3c38de5c1d55dc4bebf07a983022100'
    'b34bc7e1f1867e53c43add5b7eceb81c73026860b399a3a1793731baae9050772102bec1ec6ecb43'
    'f11f88b8d50ad82bb210091d4bdc6ea0bfd23cfc3d3bb78b4db10000000100004a48697066733a2f'
    '2f697066732f516d565261616f4469354432546d77584e47574c645852786b4b387534676d6f6b50'
    '4a694638715155504d4b4a712f6d657461646174612e6a736f6eac0000009601001976a9142322ee'
    '79e8d22496c9d62419a8c34c551f5eb25088ac0113436174686f72202f2039204c69766573203031'
    '05394c5653314031b0e6a7f0a81b61328f8a0200000000ecec46f82cbbb955347d90d04d6f5f97c8'
    '6c6b13b7cfbdff1a552f41000000006effb1f045764bc2cf04f9d3018618d3ea15d7adb4002eb3db'
    '33b2d88d6802f7'
)


def update_timestamp(tx_bytes: bytes, *, delta: int = 0) -> bytes:
    """Update timestamp to current timestamp."""
    tx = tx_or_block_from_bytes(tx_bytes)
    tx.timestamp = int(txstratum.time.time()) + delta
    return bytes(tx)


def get_timestamp(tx_bytes: bytes) -> int:
    """Get timestamp of a serialized tx."""
    tx = tx_or_block_from_bytes(tx_bytes)
    return tx.timestamp


class AppTestCase(AioHTTPTestCase):
    async def get_application(self):
        self.manager = TxMiningManager(backend=None, address=None)
        self.myapp = App(self.manager)
        return self.myapp.app

    @unittest_run_loop
    async def test_health_check(self):
        resp = await self.client.request('GET', '/health-check')
        assert resp.status == 200
        data = await resp.json()
        self.assertTrue(data['success'])

    @unittest_run_loop
    async def test_mining_status(self):
        resp = await self.client.request('GET', '/mining-status')
        assert resp.status == 200
        data = await resp.json()
        self.assertEqual(0, len(data['miners']))

    @unittest_run_loop
    async def test_job_status_missing_job_id(self):
        resp = await self.client.request('GET', '/job-status')
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'missing-job-id'}, data)

    @unittest_run_loop
    async def test_job_status_invalid_job_id(self):
        resp = await self.client.request('GET', '/job-status', params={'job-id': 'XYZ'})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'invalid-uuid'}, data)

    @unittest_run_loop
    async def test_job_status_not_found(self):
        resp = await self.client.request('GET', '/job-status', params={'job-id': '1234AB'})
        data = await resp.json()
        self.assertEqual(404, resp.status)
        self.assertEqual({'error': 'job-not-found'}, data)

    @unittest_run_loop
    async def test_submit_job_no_data(self):
        resp = await self.client.request('POST', '/submit-job')
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'cannot-decode-json'}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_json(self):
        resp = await self.client.request('POST', '/submit-job', data='abc')
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'cannot-decode-json'}, data)

    @unittest_run_loop
    async def test_submit_job_not_json_object(self):
        resp = await self.client.request('POST', '/submit-job', data='123')
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'json-must-be-an-object'}, data)

    @unittest_run_loop
    async def test_submit_job_empty_object(self):
        resp = await self.client.request('POST', '/submit-job', json={})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'missing-tx'}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_tx_format(self):
        resp = await self.client.request('POST', '/submit-job', json={'tx': 'XYZ'})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'invalid-tx'}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_tx(self):
        resp = await self.client.request('POST', '/submit-job', json={'tx': '1234'})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'invalid-tx'}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_tx_2(self):
        resp = await self.client.request('POST', '/submit-job', json={'tx': INVALID_TX_DATA.hex()})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'invalid-tx'}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_tx_timestamp1(self):
        tx_hex = update_timestamp(TX1_DATA, delta=MAX_TIMESTAMP_DELTA + 1).hex()
        resp = await self.client.request('POST', '/submit-job', json={'tx': tx_hex})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'tx-timestamp-invalid'}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_tx_timestamp2(self):
        tx_hex = update_timestamp(TX1_DATA, delta=-(MAX_TIMESTAMP_DELTA + 1)).hex()
        resp = await self.client.request('POST', '/submit-job', json={'tx': tx_hex})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'tx-timestamp-invalid'}, data)

    @unittest_run_loop
    async def test_submit_job_fix_invalid_tx_timestamp1(self):
        self.myapp.fix_invalid_timestamp = True
        tx_bytes = update_timestamp(TX1_DATA, delta=MAX_TIMESTAMP_DELTA + 1)
        resp = await self.client.request('POST', '/submit-job', json={'tx': tx_bytes.hex()})
        self.assertEqual(200, resp.status)
        data = await resp.json()
        job_id = bytes.fromhex(data['job_id'])
        job = self.manager.tx_jobs[job_id]
        tx = job.get_tx()
        self.assertNotEqual(tx.timestamp, get_timestamp(tx_bytes))
        self.assertEqual(tx.timestamp, int(txstratum.time.time()))

    @unittest_run_loop
    async def test_submit_job_fix_invalid_tx_timestamp2(self):
        self.myapp.fix_invalid_timestamp = True
        tx_bytes = update_timestamp(TX1_DATA, delta=-(MAX_TIMESTAMP_DELTA + 1))
        resp = await self.client.request('POST', '/submit-job', json={'tx': tx_bytes.hex()})
        self.assertEqual(200, resp.status)
        data = await resp.json()
        job_id = bytes.fromhex(data['job_id'])
        job = self.manager.tx_jobs[job_id]
        tx = job.get_tx()
        self.assertNotEqual(tx.timestamp, get_timestamp(tx_bytes))
        self.assertEqual(tx.timestamp, int(txstratum.time.time()))

    @unittest_run_loop
    async def test_submit_job_invalid_tx_weight(self):
        tx_bytes = update_timestamp(TX1_DATA)
        tx = tx_or_block_from_bytes(tx_bytes)
        tx.weight = MAX_TX_WEIGHT + 0.1
        tx_bytes = bytes(tx)
        tx_hex = tx_bytes.hex()
        resp = await self.client.request('POST', '/submit-job', json={'tx': tx_hex})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'tx-weight-is-too-high'}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_output_script_size(self):
        tx_bytes = update_timestamp(TX1_DATA)
        tx = tx_or_block_from_bytes(tx_bytes)
        tx.outputs[0].script = b'x' * (MAX_OUTPUT_SCRIPT_SIZE + 1)
        tx_bytes = bytes(tx)
        tx_hex = tx_bytes.hex()
        resp = await self.client.request('POST', '/submit-job', json={'tx': tx_hex})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'non-standard-tx'}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_non_standard_script(self):
        tx_bytes = update_timestamp(TX1_DATA)
        tx = tx_or_block_from_bytes(tx_bytes)
        tx.outputs[0].script = b'x' * MAX_OUTPUT_SCRIPT_SIZE
        tx_bytes = bytes(tx)
        tx_hex = tx_bytes.hex()
        resp = await self.client.request('POST', '/submit-job', json={'tx': tx_hex})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'non-standard-tx'}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_timeout(self):
        json_data = {
            'tx': update_timestamp(TX1_DATA).hex(),
            'timeout': 'x',
        }
        resp = await self.client.request('POST', '/submit-job', json=json_data)
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'invalid-timeout'}, data)

    @unittest_run_loop
    async def test_submit_job_negative_timeout(self):
        json_data = {
            'tx': update_timestamp(TX1_DATA).hex(),
            'timeout': -1,
        }
        resp = await self.client.request('POST', '/submit-job', json=json_data)
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'invalid-timeout'}, data)

    @unittest_run_loop
    async def test_cancel_job_missing_job_id(self):
        resp = await self.client.request('POST', '/cancel-job')
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'missing-job-id'}, data)

    @unittest_run_loop
    async def test_cancel_job_invalid_job_id(self):
        resp = await self.client.request('POST', '/cancel-job', params={'job-id': 'XYZ'})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({'error': 'invalid-uuid'}, data)

    @unittest_run_loop
    async def test_cancel_job_not_found(self):
        resp = await self.client.request('POST', '/cancel-job', params={'job-id': '1234'})
        data = await resp.json()
        self.assertEqual(404, resp.status)
        self.assertEqual({'error': 'job-not-found'}, data)

    @unittest_run_loop
    async def test_submit_job_success(self):
        resp = await self.client.request('POST', '/submit-job', json={'tx': update_timestamp(TX1_DATA).hex()})
        data1 = await resp.json()
        self.assertEqual(200, resp.status)

        resp = await self.client.request('GET', '/job-status', params={'job-id': data1['job_id']})
        data2 = await resp.json()
        self.assertEqual(200, resp.status)

        self.assertEqual(data1, data2)

        resp = await self.client.request('POST', '/cancel-job', params={'job-id': data1['job_id']})
        data1 = await resp.json()
        self.assertEqual(200, resp.status)

    @unittest_run_loop
    async def test_submit_job_success_token_creation(self):
        tx_hex = update_timestamp(TOKEN_CREATION_TX_DATA).hex()
        resp = await self.client.request('POST', '/submit-job', json={'tx': tx_hex})
        data1 = await resp.json()
        self.assertEqual(200, resp.status)

        resp = await self.client.request('GET', '/job-status', params={'job-id': data1['job_id']})
        data2 = await resp.json()
        self.assertEqual(200, resp.status)

        self.assertEqual(data1, data2)

        resp = await self.client.request('POST', '/cancel-job', params={'job-id': data1['job_id']})
        data1 = await resp.json()
        self.assertEqual(200, resp.status)

    @unittest_run_loop
    async def test_submit_job_success_nft_creation(self):
        tx_hex = update_timestamp(NFT_CREATION_TX_DATA).hex()
        resp = await self.client.request('POST', '/submit-job', json={'tx': tx_hex})
        data1 = await resp.json()
        self.assertEqual(200, resp.status)

        resp = await self.client.request('GET', '/job-status', params={'job-id': data1['job_id']})
        data2 = await resp.json()
        self.assertEqual(200, resp.status)

        self.assertEqual(data1, data2)

        resp = await self.client.request('POST', '/cancel-job', params={'job-id': data1['job_id']})
        data1 = await resp.json()
        self.assertEqual(200, resp.status)
