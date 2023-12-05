"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""
from unittest.mock import MagicMock

from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from hathorlib.base_transaction import tx_or_block_from_bytes

import txstratum.time
from txstratum.api import (
    MAX_OUTPUT_SCRIPT_SIZE,
    MAX_TIMESTAMP_DELTA,
    MAX_TX_WEIGHT,
    App,
)
from txstratum.manager import TxMiningManager
from txstratum.middleware import (
    HEADER_SKIP_VERSION_CHECK_KEY,
    HEADER_SKIP_VERSION_CHECK_VALUE,
    VERSION_CHECK_ERROR_MESSAGE,
)

from .tx_examples import INVALID_TX_DATA

TX1_DATA = bytes.fromhex(
    "0001000102000000000000089c0d40a9b1edfb499bc624833fde87ae459d495000393f4aaa00006"
    "a473045022100c407d5e8f411f9ae582ebd7acbfcb6ea6170332709fb69acaa34c1b426f1d8f502"
    "2003847963768eca9bcdf46e758319fb2699fd28ab657d00f54bef46c37a90405e2103755f2920f"
    "f7dc32dc5414cea1cf9e078347f40894caf0c03637d083dbb261c5c000003e800001976a914a04c"
    "9e2a0291f53c618fdad2ecb37748efb0eeeb88ac0000151800001976a914545f1156a3b00df622b"
    "1d92968c21b962e9d7aa588ac4032a8228c4020c35ed18547020000000047c9881d2bf348d5ffd6"
    "ce8398d6bc5d17b3bea75a53c15b7480be950000006ed5794bf69ebe7d7d75e7a0024d98acb85cb"
    "9c101b59b8b6073e8667c84e2ee77"
)
TX1_NONCE = "84e2ee77"

TOKEN_CREATION_TX_DATA = bytes.fromhex(
    "00020104000005551d7740fd7d3c0acc50b5677fdd844f1225985aa431e1712af2a2fd"
    "8900006a473045022100a445edb5cd6c79a0a7b5ed837582fd65b8d511ee60b64fd076"
    "e07bd8f63f75a202202dca24320bffc4c3ca2a07cdfff38f7c839bde70ed49ef634ac6"
    "588972836cab2103bfa995d676e3c0ed7b863c74cfef9683fab3163b42b6f21442326a"
    "023fc57fba0000264800001976a9146876f9578221fdb678d4e8376503098a9228b132"
    "88ac00004e2001001976a914031761ef85a24603203c97e75af355b83209f08f88ac00"
    "00000181001976a9149f091256cb98649c7c35df0aad44d7805710691e88ac00000002"
    "81001976a914b1d7a5ee505ad4d3b93ea1a5162ba83d5049ec4e88ac0109546f546865"
    "4d6f6f6e04f09f9a804034a52aec6cece75e0fc0e30200001a72272f48339fcc5d5ec5"
    "deaf197855964b0eb912e8c6eefe00928b6cf600001055641c20b71871ed2c5c7d4096"
    "a34f40888d79c25bce74421646e732dc01ff7369"
)
TOKEN_CREATION_TX_NONCE = "01ff7369"

NFT_CREATION_TX_DATA = bytes.fromhex(
    "00020102000000006effb1f045764bc2cf04f9d3018618d3ea15d7adb4002eb3db33b2d800006b48"
    "30460221008d3725e59935d0f60c63a5b5f865d845252dbdf3c38de5c1d55dc4bebf07a983022100"
    "b34bc7e1f1867e53c43add5b7eceb81c73026860b399a3a1793731baae9050772102bec1ec6ecb43"
    "f11f88b8d50ad82bb210091d4bdc6ea0bfd23cfc3d3bb78b4db10000000100004a48697066733a2f"
    "2f697066732f516d565261616f4469354432546d77584e47574c645852786b4b387534676d6f6b50"
    "4a694638715155504d4b4a712f6d657461646174612e6a736f6eac0000009601001976a9142322ee"
    "79e8d22496c9d62419a8c34c551f5eb25088ac0113436174686f72202f2039204c69766573203031"
    "05394c5653314031b0e6a7f0a81b61328f8a0200000000ecec46f82cbbb955347d90d04d6f5f97c8"
    "6c6b13b7cfbdff1a552f41000000006effb1f045764bc2cf04f9d3018618d3ea15d7adb4002eb3db"
    "33b2d88d6802f7"
)

# Transactions with more than 25 data script outputs are non standard, then won't be mined
# https://explorer.testnet.hathor.network/transaction/00b3b43c65391174b5e511bb22b13f8ef41153c43d27e25856ba18accc4f0f3c
TX_SCRIPT_DATA1 = bytes.fromhex(
    "000100010100c950858dc5810a27aad1f250180168a99b41fee85653f8c851d07f185f2806010069"
    "46304402207bbb082b356be305f5bedb1f9b21eeafeeca1c883ec6381727000f8e87787c7d022067"
    "a369bf23483cd19dfd73a0941bb3f016fbb4e0bbdfcbb5ee0240749158018f21021ec04599966661"
    "176f5e6185a7850c931464cebe6459c58a2a11a79beebd55e8000000010000060474657374ac4020"
    "0000218def41626699e50200208301f5b8cf2c953d5114725a3a67ab643af44be250e462f0a422f7"
    "76783500eaff578d1c9bb38e1b8f4dee123130f82b22c8687c5c30b5a63e45d76e9ef400000150"
)

# https://explorer.testnet.hathor.network/transaction/004027a2971cee57b98ecefbfa15247d214a56d6a5e9d90e7794e8e853664f90
TX_SCRIPT_DATA25 = bytes.fromhex(
    "000100011a001d5805e6c5129adc7099a29ac23d15541393d281fca29ec36222efb5f934ad00006a"
    "473045022100b9329f099d1ac7bc191fe0102d7397be9b5f56308c04a25420422f783817f1a40220"
    "190d9c91f7d17b62b0fb247f0282740e68f472ee54ce7708694a2664f3153cac2103ecacef4a15a3"
    "818c2c70a0f8784f02fed566192f855f06b9f062020ceab91a98000000010000060474657374ac00"
    "0000010000060474657374ac000000010000060474657374ac000000010000060474657374ac0000"
    "00010000060474657374ac000000010000060474657374ac000000010000060474657374ac000000"
    "010000060474657374ac000000010000060474657374ac000000010000060474657374ac00000001"
    "0000060474657374ac000000010000060474657374ac000000010000060474657374ac0000000100"
    "00060474657374ac000000010000060474657374ac000000010000060474657374ac000000010000"
    "060474657374ac000000010000060474657374ac000000010000060474657374ac00000001000006"
    "0474657374ac000000010000060474657374ac000000010000060474657374ac0000000100000604"
    "74657374ac000000010000060474657374ac000000010000060474657374ac0000001000001976a9"
    "149e2cd235e1cd2a4fac3cb2394b9806403cf1862f88ac40200000218def4162669a100200b3b43c"
    "65391174b5e511bb22b13f8ef41153c43d27e25856ba18accc4f0f3c00208301f5b8cf2c953d5114"
    "725a3a67ab643af44be250e462f0a422f77678350000004f"
)

# https://explorer.testnet.hathor.network/transaction/00204c174e1011a4264b7c930e434da9af3dfe01442876a0590ec6cbb97a8597
TX_SCRIPT_DATA26 = bytes.fromhex(
    "000100011b00933e98cf9975a0d7850af4a49a14848c351929ca4ef22996da9f0215779e6401006a"
    "47304502210099736f4048a6eef587e42d37b063adad3b9b6a53399e6f3636cfb8ee364fa5f30220"
    "4c16776f07fdf65cee38120f41f2466c7b723efa0fcd06eca73b6c4b49d54e5b2103a2b3d37f580e"
    "f4f04a8b22c7613d570d9431d9d4ad1a0fe99c80da8b56ce806c000000010000060474657374ac00"
    "0000010000060474657374ac000000010000060474657374ac000000010000060474657374ac0000"
    "00010000060474657374ac000000010000060474657374ac000002ba00001976a914b33763a9c9ea"
    "227e5c0ffaad6729128a6d293f7688ac000000010000060474657374ac0000000100000604746573"
    "74ac000000010000060474657374ac000000010000060474657374ac000000010000060474657374"
    "ac000000010000060474657374ac000000010000060474657374ac000000010000060474657374ac"
    "000000010000060474657374ac000000010000060474657374ac000000010000060474657374ac00"
    "0000010000060474657374ac000000010000060474657374ac000000010000060474657374ac0000"
    "00010000060474657374ac000000010000060474657374ac000000010000060474657374ac000000"
    "010000060474657374ac000000010000060474657374ac000000010000060474657374ac40200000"
    "218def416266af4302004027a2971cee57b98ecefbfa15247d214a56d6a5e9d90e7794e8e853664f"
    "9000b3b43c65391174b5e511bb22b13f8ef41153c43d27e25856ba18accc4f0f3c00000083"
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


class BaseAppTestCase(AioHTTPTestCase):
    __test__ = False
    version_check: bool

    @unittest_run_loop
    async def test_health_check(self):
        health_check_result = MagicMock()
        health_check_result.get_http_status_code.return_value = 200
        health_check_result.to_json.return_value = {"status": "pass"}

        async def side_effect():
            return health_check_result

        self.healthcheck.get_health_check.side_effect = side_effect

        resp = await self.client.request("GET", "/health")
        assert resp.status == 200
        data = await resp.json()
        self.assertEqual(data, {"status": "pass"})

    @unittest_run_loop
    async def test_mining_status(self):
        resp = await self.client.request("GET", "/mining-status")
        assert resp.status == 200
        data = await resp.json()
        self.assertEqual(0, len(data["miners"]))

    @unittest_run_loop
    async def test_job_status_missing_job_id(self):
        resp = await self.client.request("GET", "/job-status")
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "missing-job-id"}, data)

    @unittest_run_loop
    async def test_job_status_invalid_job_id(self):
        resp = await self.client.request("GET", "/job-status", params={"job-id": "XYZ"})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "invalid-uuid"}, data)

    @unittest_run_loop
    async def test_job_status_not_found(self):
        resp = await self.client.request(
            "GET", "/job-status", params={"job-id": "1234AB"}
        )
        data = await resp.json()
        self.assertEqual(404, resp.status)
        self.assertEqual({"error": "job-not-found"}, data)

    @unittest_run_loop
    async def test_submit_job_no_data(self):
        resp = await self.client.request("POST", "/submit-job")
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "cannot-decode-json"}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_json(self):
        resp = await self.client.request("POST", "/submit-job", data="abc")
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "cannot-decode-json"}, data)

    @unittest_run_loop
    async def test_submit_job_not_json_object(self):
        resp = await self.client.request("POST", "/submit-job", data="123")
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "json-must-be-an-object"}, data)

    @unittest_run_loop
    async def test_submit_job_empty_object(self):
        resp = await self.client.request("POST", "/submit-job", json={})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "missing-tx"}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_tx_format(self):
        resp = await self.client.request("POST", "/submit-job", json={"tx": "XYZ"})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "invalid-tx"}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_tx(self):
        resp = await self.client.request("POST", "/submit-job", json={"tx": "1234"})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "invalid-tx"}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_tx_2(self):
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": INVALID_TX_DATA.hex()}
        )
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "invalid-tx"}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_tx_timestamp1(self):
        tx_hex = update_timestamp(TX1_DATA, delta=MAX_TIMESTAMP_DELTA + 1).hex()
        resp = await self.client.request("POST", "/submit-job", json={"tx": tx_hex})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "tx-timestamp-invalid"}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_tx_timestamp2(self):
        tx_hex = update_timestamp(TX1_DATA, delta=-(MAX_TIMESTAMP_DELTA + 1)).hex()
        resp = await self.client.request("POST", "/submit-job", json={"tx": tx_hex})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "tx-timestamp-invalid"}, data)

    @unittest_run_loop
    async def test_submit_job_fix_invalid_tx_timestamp1(self):
        self.myapp.fix_invalid_timestamp = True
        tx_bytes = update_timestamp(TX1_DATA, delta=MAX_TIMESTAMP_DELTA + 1)
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_bytes.hex()}
        )
        self.assertEqual(200, resp.status)
        data = await resp.json()
        job_id = bytes.fromhex(data["job_id"])
        job = self.manager.tx_jobs[job_id]
        tx = job.get_tx()
        self.assertNotEqual(tx.timestamp, get_timestamp(tx_bytes))
        self.assertEqual(tx.timestamp, int(txstratum.time.time()))

    @unittest_run_loop
    async def test_submit_job_fix_invalid_tx_timestamp2(self):
        self.myapp.fix_invalid_timestamp = True
        tx_bytes = update_timestamp(TX1_DATA, delta=-(MAX_TIMESTAMP_DELTA + 1))
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_bytes.hex()}
        )
        self.assertEqual(200, resp.status)
        data = await resp.json()
        job_id = bytes.fromhex(data["job_id"])
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
        resp = await self.client.request("POST", "/submit-job", json={"tx": tx_hex})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "tx-weight-is-too-high"}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_output_script_size(self):
        tx_bytes = update_timestamp(TX1_DATA)
        tx = tx_or_block_from_bytes(tx_bytes)
        tx.outputs[0].script = b"x" * (MAX_OUTPUT_SCRIPT_SIZE + 1)
        tx_bytes = bytes(tx)
        tx_hex = tx_bytes.hex()
        resp = await self.client.request("POST", "/submit-job", json={"tx": tx_hex})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "non-standard-tx"}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_non_standard_script(self):
        tx_bytes = update_timestamp(TX1_DATA)
        tx = tx_or_block_from_bytes(tx_bytes)
        tx.outputs[0].script = b"x" * MAX_OUTPUT_SCRIPT_SIZE
        tx_bytes = bytes(tx)
        tx_hex = tx_bytes.hex()
        resp = await self.client.request("POST", "/submit-job", json={"tx": tx_hex})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "non-standard-tx"}, data)

    @unittest_run_loop
    async def test_submit_job_invalid_timeout(self):
        json_data = {
            "tx": update_timestamp(TX1_DATA).hex(),
            "timeout": "x",
        }
        resp = await self.client.request("POST", "/submit-job", json=json_data)
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "invalid-timeout"}, data)

    @unittest_run_loop
    async def test_submit_job_negative_timeout(self):
        json_data = {
            "tx": update_timestamp(TX1_DATA).hex(),
            "timeout": -1,
        }
        resp = await self.client.request("POST", "/submit-job", json=json_data)
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "invalid-timeout"}, data)

    @unittest_run_loop
    async def test_cancel_job_missing_job_id(self):
        resp = await self.client.request("POST", "/cancel-job")
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "missing-job-id"}, data)

    @unittest_run_loop
    async def test_cancel_job_invalid_job_id(self):
        resp = await self.client.request(
            "POST", "/cancel-job", params={"job-id": "XYZ"}
        )
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "invalid-uuid"}, data)

    @unittest_run_loop
    async def test_cancel_job_not_found(self):
        resp = await self.client.request(
            "POST", "/cancel-job", params={"job-id": "1234"}
        )
        data = await resp.json()
        self.assertEqual(404, resp.status)
        self.assertEqual({"error": "job-not-found"}, data)

    @unittest_run_loop
    async def test_submit_job_success_then_resubmit(self):
        body = {"tx": update_timestamp(TX1_DATA).hex()}

        resp = await self.client.request("POST", "/submit-job", json=body)
        data1 = await resp.json()
        self.assertEqual(200, resp.status)

        resp = await self.client.request("POST", "/submit-job", json=body)
        data2 = await resp.json()
        self.assertEqual(200, resp.status)

        # Submit the same job 2 times should return the data on the job both times
        self.assertEqual(data1, data2)

        resp = await self.client.request(
            "GET", "/job-status", params={"job-id": data1["job_id"]}
        )
        data3 = await resp.json()
        self.assertEqual(200, resp.status)

        self.assertEqual(data1, data3)

        resp = await self.client.request(
            "POST", "/cancel-job", params={"job-id": data1["job_id"]}
        )
        self.assertEqual(200, resp.status)

    @unittest_run_loop
    async def test_submit_job_success(self):
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": update_timestamp(TX1_DATA).hex()}
        )
        data1 = await resp.json()
        self.assertEqual(200, resp.status)

        resp = await self.client.request(
            "GET", "/job-status", params={"job-id": data1["job_id"]}
        )
        data2 = await resp.json()
        self.assertEqual(200, resp.status)

        self.assertEqual(data1, data2)

        resp = await self.client.request(
            "POST", "/cancel-job", params={"job-id": data1["job_id"]}
        )
        self.assertEqual(200, resp.status)

    @unittest_run_loop
    async def test_submit_job_success_token_creation(self):
        tx_hex = update_timestamp(TOKEN_CREATION_TX_DATA).hex()
        resp = await self.client.request("POST", "/submit-job", json={"tx": tx_hex})
        data1 = await resp.json()
        self.assertEqual(200, resp.status)

        resp = await self.client.request(
            "GET", "/job-status", params={"job-id": data1["job_id"]}
        )
        data2 = await resp.json()
        self.assertEqual(200, resp.status)

        self.assertEqual(data1, data2)

        resp = await self.client.request(
            "POST", "/cancel-job", params={"job-id": data1["job_id"]}
        )
        data1 = await resp.json()
        self.assertEqual(200, resp.status)

    @unittest_run_loop
    async def test_submit_job_success_nft_creation(self):
        tx_hex = update_timestamp(NFT_CREATION_TX_DATA).hex()
        resp = await self.client.request("POST", "/submit-job", json={"tx": tx_hex})
        data1 = await resp.json()
        self.assertEqual(200, resp.status)

        resp = await self.client.request(
            "GET", "/job-status", params={"job-id": data1["job_id"]}
        )
        data2 = await resp.json()
        self.assertEqual(200, resp.status)

        self.assertEqual(data1, data2)

        resp = await self.client.request(
            "POST", "/cancel-job", params={"job-id": data1["job_id"]}
        )
        data1 = await resp.json()
        self.assertEqual(200, resp.status)

    @unittest_run_loop
    async def test_submit_job_success_script_data1(self):
        tx_hex = update_timestamp(TX_SCRIPT_DATA1).hex()
        resp = await self.client.request("POST", "/submit-job", json={"tx": tx_hex})
        data1 = await resp.json()
        self.assertEqual(200, resp.status)

        resp = await self.client.request(
            "GET", "/job-status", params={"job-id": data1["job_id"]}
        )
        data2 = await resp.json()
        self.assertEqual(200, resp.status)

        self.assertEqual(data1, data2)

        resp = await self.client.request(
            "POST", "/cancel-job", params={"job-id": data1["job_id"]}
        )
        data1 = await resp.json()
        self.assertEqual(200, resp.status)

    @unittest_run_loop
    async def test_submit_job_success_script_data25(self):
        tx_hex = update_timestamp(TX_SCRIPT_DATA25).hex()
        resp = await self.client.request("POST", "/submit-job", json={"tx": tx_hex})
        data1 = await resp.json()
        self.assertEqual(200, resp.status)

        resp = await self.client.request(
            "GET", "/job-status", params={"job-id": data1["job_id"]}
        )
        data2 = await resp.json()
        self.assertEqual(200, resp.status)

        self.assertEqual(data1, data2)

        resp = await self.client.request(
            "POST", "/cancel-job", params={"job-id": data1["job_id"]}
        )
        data1 = await resp.json()
        self.assertEqual(200, resp.status)

    @unittest_run_loop
    async def test_submit_job_fail_script_data26_non_standard(self):
        tx_hex = update_timestamp(TX_SCRIPT_DATA26).hex()
        resp = await self.client.request("POST", "/submit-job", json={"tx": tx_hex})
        data = await resp.json()
        self.assertEqual(400, resp.status)
        self.assertEqual({"error": "non-standard-tx"}, data)

    @unittest_run_loop
    async def test_check_wallet_version(self):
        header_skip_version_check = {
            HEADER_SKIP_VERSION_CHECK_KEY: HEADER_SKIP_VERSION_CHECK_VALUE
        }

        tx_hex = update_timestamp(TX_SCRIPT_DATA25).hex()

        header_desktop_lower = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
                "HathorWallet/0.22.1 Chrome/73.0.3683.121 Electron/5.0.13 Safari/537.36 HathorWallet/0.22.1"
            )
        }
        header_desktop_equal = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
                "HathorWallet/0.23.0 Chrome/73.0.3683.121 Electron/5.0.13 Safari/537.36 HathorWallet/0.23.0"
            )
        }
        header_desktop_higher = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
                "HathorWallet/0.23.1 Chrome/73.0.3683.121 Electron/5.0.13 Safari/537.36 HathorWallet/0.23.1"
            )
        }

        # Success with desktop version higher than the minimum
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_hex}, headers=header_desktop_higher
        )
        await resp.json()
        self.assertEqual(200, resp.status)

        # Success with desktop version equal to the minimum
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_hex}, headers=header_desktop_equal
        )
        await resp.json()
        self.assertEqual(200, resp.status)

        # Error with desktop version lower than the minimum if the app is validating desktop version
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_hex}, headers=header_desktop_lower
        )
        data = await resp.json()
        if self.version_check:
            self.assertEqual(400, resp.status)
            self.assertEqual(
                {
                    "error": VERSION_CHECK_ERROR_MESSAGE,
                    "data": {"min_version": "0.23.0", "version": "0.22.1"},
                },
                data,
            )
        else:
            self.assertEqual(200, resp.status)

        # Success always with skip version check header
        resp = await self.client.request(
            "POST",
            "/submit-job",
            json={"tx": tx_hex},
            headers={**header_desktop_lower, **header_skip_version_check},
        )
        await resp.json()
        self.assertEqual(200, resp.status)

        header_mobile_lower = {"User-Agent": "Hathor Wallet Mobile / 0.18.0"}
        header_mobile_equal = {"User-Agent": "Hathor Wallet Mobile / 1.18.3"}
        header_mobile_higher = {"User-Agent": "Hathor Wallet Mobile / 20.1.0"}
        header_mobile_custom = {"User-Agent": "HathorMobile/1"}
        header_mobile_custom_wrong = {"User-Agent": "HathorMobile/2"}

        # Success with mobile version higher than the minimum
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_hex}, headers=header_mobile_higher
        )
        await resp.json()
        self.assertEqual(200, resp.status)

        # Success with mobile version equal to the minimum
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_hex}, headers=header_mobile_equal
        )
        await resp.json()
        self.assertEqual(200, resp.status)

        # Error with mobile version lower than the minimum if the app is validating mobile version
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_hex}, headers=header_mobile_lower
        )
        data = await resp.json()
        if self.version_check:
            self.assertEqual(400, resp.status)
            self.assertEqual(
                {
                    "error": VERSION_CHECK_ERROR_MESSAGE,
                    "data": {"min_version": "1.18.3", "version": "0.18.0"},
                },
                data,
            )
        else:
            self.assertEqual(200, resp.status)

        # Success always with skip version check header
        resp = await self.client.request(
            "POST",
            "/submit-job",
            json={"tx": tx_hex},
            headers={**header_mobile_lower, **header_skip_version_check},
        )
        await resp.json()
        self.assertEqual(200, resp.status)

        # Error with mobile version before v0.18.0, if the app is validating mobile version
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_hex}, headers=header_mobile_custom
        )
        data = await resp.json()
        if self.version_check:
            self.assertEqual(400, resp.status)
            self.assertEqual(
                {
                    "error": VERSION_CHECK_ERROR_MESSAGE,
                    "data": {"min_version": "1.18.3"},
                },
                data,
            )
        else:
            self.assertEqual(200, resp.status)

        # Success always with skip version check header
        resp = await self.client.request(
            "POST",
            "/submit-job",
            json={"tx": tx_hex},
            headers={**header_mobile_custom, **header_skip_version_check},
        )
        await resp.json()
        self.assertEqual(200, resp.status)

        # Success if the custom is different than expected
        resp = await self.client.request(
            "POST",
            "/submit-job",
            json={"tx": tx_hex},
            headers=header_mobile_custom_wrong,
        )
        await resp.json()
        self.assertEqual(200, resp.status)

        header_headless_lower = {"User-Agent": "Hathor Wallet Headless / 0.14.87"}
        header_headless_equal = {"User-Agent": "Hathor Wallet Headless / 0.14.88"}
        header_headless_higher = {"User-Agent": "Hathor Wallet Headless / 0.14.89"}

        # Success with headless version higher than the minimum
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_hex}, headers=header_headless_higher
        )
        await resp.json()
        self.assertEqual(200, resp.status)

        # Success with headless version equal to the minimum
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_hex}, headers=header_headless_equal
        )
        await resp.json()
        self.assertEqual(200, resp.status)

        # Error with headless version lower than the minimum if the app is validating headless version
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_hex}, headers=header_headless_lower
        )
        data = await resp.json()
        if self.version_check:
            self.assertEqual(400, resp.status)
            self.assertEqual(
                {
                    "error": VERSION_CHECK_ERROR_MESSAGE,
                    "data": {"min_version": "0.14.88", "version": "0.14.87"},
                },
                data,
            )
        else:
            self.assertEqual(200, resp.status)

        # Success always with skip version check header
        resp = await self.client.request(
            "POST",
            "/submit-job",
            json={"tx": tx_hex},
            headers={**header_headless_lower, **header_skip_version_check},
        )
        await resp.json()
        self.assertEqual(200, resp.status)

        header_headless_wrong1 = {"User-Agent": "Hathor Wallet Headless / 0.xx.88"}
        header_headless_wrong2 = {"User-Agent": "Hathor Wallet Headless / 0.14.88beta"}

        # Success in both cases because the regex shouldn't identify this as headless versions
        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_hex}, headers=header_headless_wrong1
        )
        await resp.json()
        self.assertEqual(200, resp.status)

        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_hex}, headers=header_headless_wrong2
        )
        await resp.json()
        self.assertEqual(200, resp.status)


class AppTestCase(BaseAppTestCase):
    __test__ = True

    async def get_application(self):
        self.manager = TxMiningManager(backend=None, pubsub=MagicMock(), address=None)
        self.healthcheck = MagicMock()
        self.myapp = App(self.manager, self.healthcheck)
        self.version_check = False
        return self.myapp.app


class AppVersionCheckTestCase(BaseAppTestCase):
    __test__ = True

    async def get_application(self):
        self.manager = TxMiningManager(backend=None, pubsub=MagicMock(), address=None)
        self.healthcheck = MagicMock()
        self.myapp = App(
            self.manager,
            self.healthcheck,
            min_wallet_desktop_version="0.23.0",
            min_wallet_mobile_version="1.18.3",
            min_wallet_headless_version="0.14.88",
        )
        self.version_check = True
        return self.myapp.app
