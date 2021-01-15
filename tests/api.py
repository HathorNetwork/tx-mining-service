"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from txstratum.api import App
from txstratum.manager import TxMiningManager

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


class AppTestCase(AioHTTPTestCase):
    async def get_application(self):
        self.manager = TxMiningManager(backend=None, address=None)
        app = App(self.manager)
        return app.app

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
        resp = await self.client.request('POST', '/submit-job', json={'tx': TX1_DATA.hex()})
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
        resp = await self.client.request('POST', '/submit-job', json={'tx': TOKEN_CREATION_TX_DATA.hex()})
        data1 = await resp.json()
        self.assertEqual(200, resp.status)

        resp = await self.client.request('GET', '/job-status', params={'job-id': data1['job_id']})
        data2 = await resp.json()
        self.assertEqual(200, resp.status)

        self.assertEqual(data1, data2)

        resp = await self.client.request('POST', '/cancel-job', params={'job-id': data1['job_id']})
        data1 = await resp.json()
        self.assertEqual(200, resp.status)
