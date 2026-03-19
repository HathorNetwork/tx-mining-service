"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.

Tests for mining transactions with shielded outputs.

These tests validate that the tx-mining-service can correctly:
1. Parse transactions containing ShieldedOutputsHeader
2. Solve PoW for transactions with shielded outputs (both AmountShielded and FullShielded)
3. Round-trip serialize/deserialize transactions with shielded outputs
4. Mine shielded transactions through the dev-miner HTTP API
"""

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock

from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from hathorlib import Transaction
from hathorlib.base_transaction import tx_or_block_from_bytes
from hathorlib.headers.shielded_outputs_header import ShieldedOutputsHeader
from hathorlib.transaction.shielded_tx_output import (
    AmountShieldedOutput,
    FullShieldedOutput,
    OutputMode,
    deserialize_shielded_output,
    serialize_shielded_output,
)

import txstratum.time
from txstratum.api import App
from txstratum.dev.manager import DevMiningManager
from txstratum.dev.tx_miner import solve_tx
from txstratum.jobs import TxJob

# A real serialized transaction (same as test_dev_miner.py) used as a base.
BASE_TX_DATA = bytes.fromhex(
    "0001000102000000000000089c0d40a9b1edfb499bc624833fde87ae459d495000393f4aaa00006"
    "a473045022100c407d5e8f411f9ae582ebd7acbfcb6ea6170332709fb69acaa34c1b426f1d8f502"
    "2003847963768eca9bcdf46e758319fb2699fd28ab657d00f54bef46c37a90405e2103755f2920f"
    "f7dc32dc5414cea1cf9e078347f40894caf0c03637d083dbb261c5c000003e800001976a914a04c"
    "9e2a0291f53c618fdad2ecb37748efb0eeeb88ac0000151800001976a914545f1156a3b00df622b"
    "1d92968c21b962e9d7aa588ac4032a8228c4020c35ed18547020000000047c9881d2bf348d5ffd6"
    "ce8398d6bc5d17b3bea75a53c15b7480be950000006ed5794bf69ebe7d7d75e7a0024d98acb85cb"
    "9c101b59b8b6073e8667c84e2ee77"
)

# Fake 33-byte compressed public key (starts with 0x02)
FAKE_COMMITMENT = b'\x02' + b'\xab' * 32
FAKE_ASSET_COMMITMENT = b'\x03' + b'\xcd' * 32
FAKE_EPHEMERAL_PUBKEY = b'\x02' + b'\xef' * 32

# Fake range proof (~675 bytes in reality, use short placeholder for tests)
FAKE_RANGE_PROOF = os.urandom(675)

# Fake surjection proof
FAKE_SURJECTION_PROOF = os.urandom(130)

# Simple P2PKH locking script
FAKE_SCRIPT = bytes.fromhex("76a914a04c9e2a0291f53c618fdad2ecb37748efb0eeeb88ac")


def _make_amount_shielded_output(token_data: int = 0) -> AmountShieldedOutput:
    """Create a test AmountShieldedOutput."""
    return AmountShieldedOutput(
        commitment=FAKE_COMMITMENT,
        range_proof=FAKE_RANGE_PROOF,
        script=FAKE_SCRIPT,
        token_data=token_data,
        ephemeral_pubkey=FAKE_EPHEMERAL_PUBKEY,
    )


def _make_full_shielded_output() -> FullShieldedOutput:
    """Create a test FullShieldedOutput."""
    return FullShieldedOutput(
        commitment=FAKE_COMMITMENT,
        range_proof=FAKE_RANGE_PROOF,
        script=FAKE_SCRIPT,
        asset_commitment=FAKE_ASSET_COMMITMENT,
        surjection_proof=FAKE_SURJECTION_PROOF,
        ephemeral_pubkey=FAKE_EPHEMERAL_PUBKEY,
    )


def _build_tx_with_shielded_outputs(shielded_outputs: list) -> bytes:
    """Build a serialized transaction with shielded outputs appended as a header.

    Takes the base transaction bytes and appends a ShieldedOutputsHeader.
    """
    tx = tx_or_block_from_bytes(BASE_TX_DATA)
    assert isinstance(tx, Transaction)

    # Update timestamp to current time
    tx.timestamp = int(txstratum.time.time())

    # Build the shielded outputs header manually by constructing the header
    # and attaching it to the transaction.
    header = ShieldedOutputsHeader(tx=tx, shielded_outputs=shielded_outputs)
    tx.headers = [header]

    # Serialize: this produces funds + graph + nonce + headers
    tx.update_hash()
    return bytes(tx)


# ---------------------------------------------------------------------------
# Shielded output serialization tests
# ---------------------------------------------------------------------------


class TestShieldedOutputSerialization(unittest.TestCase):
    """Test round-trip serialization of shielded outputs."""

    __test__ = True

    def test_amount_shielded_roundtrip(self):
        """AmountShieldedOutput survives serialize -> deserialize."""
        original = _make_amount_shielded_output()
        data = serialize_shielded_output(original)
        restored, remaining = deserialize_shielded_output(data)

        self.assertEqual(remaining, b'')
        self.assertIsInstance(restored, AmountShieldedOutput)
        self.assertEqual(restored.commitment, original.commitment)
        self.assertEqual(restored.range_proof, original.range_proof)
        self.assertEqual(restored.script, original.script)
        self.assertEqual(restored.token_data, original.token_data)
        self.assertEqual(restored.ephemeral_pubkey, original.ephemeral_pubkey)

    def test_full_shielded_roundtrip(self):
        """FullShieldedOutput survives serialize -> deserialize."""
        original = _make_full_shielded_output()
        data = serialize_shielded_output(original)
        restored, remaining = deserialize_shielded_output(data)

        self.assertEqual(remaining, b'')
        self.assertIsInstance(restored, FullShieldedOutput)
        self.assertEqual(restored.commitment, original.commitment)
        self.assertEqual(restored.range_proof, original.range_proof)
        self.assertEqual(restored.script, original.script)
        self.assertEqual(restored.asset_commitment, original.asset_commitment)
        self.assertEqual(restored.surjection_proof, original.surjection_proof)
        self.assertEqual(restored.ephemeral_pubkey, original.ephemeral_pubkey)

    def test_mode_byte_amount_only(self):
        """AmountShieldedOutput serializes with mode byte 0x01."""
        output = _make_amount_shielded_output()
        data = serialize_shielded_output(output)
        self.assertEqual(data[0], OutputMode.AMOUNT_ONLY)

    def test_mode_byte_fully_shielded(self):
        """FullShieldedOutput serializes with mode byte 0x02."""
        output = _make_full_shielded_output()
        data = serialize_shielded_output(output)
        self.assertEqual(data[0], OutputMode.FULLY_SHIELDED)


# ---------------------------------------------------------------------------
# Transaction with shielded outputs: parsing and PoW
# ---------------------------------------------------------------------------


class TestShieldedTxParsing(unittest.TestCase):
    """Test that transactions with shielded outputs can be parsed and mined."""

    __test__ = True

    def test_parse_tx_with_amount_shielded_outputs(self):
        """A transaction with AmountShieldedOutputs can be serialized and parsed back."""
        outputs = [_make_amount_shielded_output(), _make_amount_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)

        # Parse it back
        tx = tx_or_block_from_bytes(tx_bytes)
        self.assertIsInstance(tx, Transaction)
        self.assertTrue(tx.has_shielded_outputs())

        header = tx.get_shielded_outputs_header()
        self.assertEqual(len(header.shielded_outputs), 2)
        for so in header.shielded_outputs:
            self.assertIsInstance(so, AmountShieldedOutput)
            self.assertEqual(so.commitment, FAKE_COMMITMENT)

    def test_parse_tx_with_full_shielded_outputs(self):
        """A transaction with FullShieldedOutputs can be serialized and parsed back."""
        outputs = [_make_full_shielded_output(), _make_full_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)

        tx = tx_or_block_from_bytes(tx_bytes)
        self.assertIsInstance(tx, Transaction)
        self.assertTrue(tx.has_shielded_outputs())

        header = tx.get_shielded_outputs_header()
        self.assertEqual(len(header.shielded_outputs), 2)
        for so in header.shielded_outputs:
            self.assertIsInstance(so, FullShieldedOutput)
            self.assertEqual(so.asset_commitment, FAKE_ASSET_COMMITMENT)

    def test_parse_tx_with_mixed_shielded_outputs(self):
        """A transaction with both AmountShielded and FullShielded outputs parses correctly."""
        outputs = [_make_amount_shielded_output(), _make_full_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)

        tx = tx_or_block_from_bytes(tx_bytes)
        self.assertTrue(tx.has_shielded_outputs())

        header = tx.get_shielded_outputs_header()
        self.assertEqual(len(header.shielded_outputs), 2)
        self.assertIsInstance(header.shielded_outputs[0], AmountShieldedOutput)
        self.assertIsInstance(header.shielded_outputs[1], FullShieldedOutput)

    def test_tx_roundtrip_preserves_bytes(self):
        """Serializing and deserializing a shielded tx produces identical bytes."""
        outputs = [_make_amount_shielded_output(), _make_full_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)

        tx = tx_or_block_from_bytes(tx_bytes)
        reserialized = bytes(tx)
        self.assertEqual(tx_bytes, reserialized)

    def test_solve_tx_with_shielded_outputs_trivial_weight(self):
        """solve_tx finds a valid PoW for a transaction with shielded outputs (weight=1)."""
        outputs = [_make_amount_shielded_output(), _make_amount_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)

        tx = tx_or_block_from_bytes(tx_bytes)
        tx.weight = 1.0
        result = solve_tx(tx)
        self.assertTrue(result)
        self.assertTrue(tx.verify_pow())

    def test_solve_tx_with_full_shielded_trivial_weight(self):
        """solve_tx works for FullShieldedOutputs too."""
        outputs = [_make_full_shielded_output(), _make_full_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)

        tx = tx_or_block_from_bytes(tx_bytes)
        tx.weight = 1.0
        result = solve_tx(tx)
        self.assertTrue(result)
        self.assertTrue(tx.verify_pow())

    def test_shielded_outputs_affect_hash(self):
        """Adding shielded outputs changes the transaction hash.

        This verifies that the ShieldedOutputsHeader is included in the hash
        computation, which is critical for mining correctness.
        """
        # Transaction without shielded outputs
        tx_plain = tx_or_block_from_bytes(BASE_TX_DATA)
        tx_plain.timestamp = int(txstratum.time.time())
        tx_plain.update_hash()
        hash_plain = tx_plain.hash

        # Same transaction with shielded outputs
        outputs = [_make_amount_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)
        tx_shielded = tx_or_block_from_bytes(tx_bytes)
        hash_shielded = tx_shielded.hash

        self.assertNotEqual(hash_plain, hash_shielded)


# ---------------------------------------------------------------------------
# Dev-miner integration: mine shielded txs through the HTTP API
# ---------------------------------------------------------------------------


class TestDevMinerShieldedOutputs(AioHTTPTestCase):
    """Test mining transactions with shielded outputs through the dev-miner HTTP API.

    This validates the full lifecycle: submit-job with a shielded tx → poll → done.
    """

    __test__ = True

    async def get_application(self):
        self.backend = MagicMock()
        self.backend.get_tx_parents = AsyncMock(
            return_value=[b"\x00" * 32, b"\x01" * 32]
        )
        self.backend.push_tx_or_block = AsyncMock(return_value=True)
        self.manager = DevMiningManager(backend=self.backend)
        await self.manager.start()
        self.healthcheck = MagicMock()
        self.myapp = App(self.manager, self.healthcheck)
        return self.myapp.app

    @unittest_run_loop
    async def test_submit_tx_with_amount_shielded_outputs(self):
        """A transaction with AmountShieldedOutputs can be submitted and mined."""
        outputs = [_make_amount_shielded_output(), _make_amount_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)

        # Verify the tx parses as having shielded outputs
        tx = tx_or_block_from_bytes(tx_bytes)
        self.assertTrue(tx.has_shielded_outputs())

        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_bytes.hex()}
        )
        self.assertEqual(200, resp.status)
        data = await resp.json()
        job_id = data["job_id"]

        # Poll for completion
        for _ in range(50):
            await asyncio.sleep(0.1)
            resp = await self.client.request(
                "GET", "/job-status", params={"job-id": job_id}
            )
            data = await resp.json()
            if data["status"] == "done":
                break

        self.assertEqual("done", data["status"])
        self.assertIsNotNone(data["tx"]["nonce"])

    @unittest_run_loop
    async def test_submit_tx_with_full_shielded_outputs(self):
        """A transaction with FullShieldedOutputs can be submitted and mined."""
        outputs = [_make_full_shielded_output(), _make_full_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)

        tx = tx_or_block_from_bytes(tx_bytes)
        self.assertTrue(tx.has_shielded_outputs())

        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_bytes.hex()}
        )
        self.assertEqual(200, resp.status)
        data = await resp.json()
        job_id = data["job_id"]

        for _ in range(50):
            await asyncio.sleep(0.1)
            resp = await self.client.request(
                "GET", "/job-status", params={"job-id": job_id}
            )
            data = await resp.json()
            if data["status"] == "done":
                break

        self.assertEqual("done", data["status"])
        self.assertIsNotNone(data["tx"]["nonce"])

    @unittest_run_loop
    async def test_submit_tx_with_mixed_shielded_outputs(self):
        """A transaction with both output types can be submitted and mined."""
        outputs = [_make_amount_shielded_output(), _make_full_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)

        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_bytes.hex()}
        )
        self.assertEqual(200, resp.status)
        data = await resp.json()
        job_id = data["job_id"]

        for _ in range(50):
            await asyncio.sleep(0.1)
            resp = await self.client.request(
                "GET", "/job-status", params={"job-id": job_id}
            )
            data = await resp.json()
            if data["status"] == "done":
                break

        self.assertEqual("done", data["status"])

    @unittest_run_loop
    async def test_mined_shielded_tx_preserves_header(self):
        """After mining, the solved transaction still contains the shielded outputs header."""
        outputs = [_make_amount_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)

        resp = await self.client.request(
            "POST", "/submit-job", json={"tx": tx_bytes.hex()}
        )
        data = await resp.json()
        job_id = data["job_id"]

        for _ in range(50):
            await asyncio.sleep(0.1)
            resp = await self.client.request(
                "GET", "/job-status", params={"job-id": job_id}
            )
            data = await resp.json()
            if data["status"] == "done":
                break

        self.assertEqual("done", data["status"])

        # Retrieve the job from the manager and verify the tx still has shielded outputs
        job_uuid = bytes.fromhex(job_id)
        job = self.manager.tx_jobs.get(job_uuid)
        self.assertIsNotNone(job)
        mined_tx = job.get_tx()
        self.assertIsInstance(mined_tx, Transaction)
        self.assertTrue(mined_tx.has_shielded_outputs())

        header = mined_tx.get_shielded_outputs_header()
        self.assertEqual(len(header.shielded_outputs), 1)
        self.assertIsInstance(header.shielded_outputs[0], AmountShieldedOutput)

    @unittest_run_loop
    async def test_propagate_shielded_tx(self):
        """A shielded tx with propagate=True is pushed to the fullnode after mining."""
        outputs = [_make_amount_shielded_output(), _make_amount_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)

        resp = await self.client.request(
            "POST",
            "/submit-job",
            json={"tx": tx_bytes.hex(), "propagate": True},
        )
        self.assertEqual(200, resp.status)
        data = await resp.json()
        job_id = data["job_id"]

        for _ in range(50):
            await asyncio.sleep(0.1)
            resp = await self.client.request(
                "GET", "/job-status", params={"job-id": job_id}
            )
            data = await resp.json()
            if data["status"] == "done":
                break

        self.assertEqual("done", data["status"])
        self.backend.push_tx_or_block.assert_called_once()

    @unittest_run_loop
    async def test_shielded_tx_with_add_parents(self):
        """A shielded tx with add_parents=True fetches parents before mining."""
        outputs = [_make_full_shielded_output(), _make_full_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)

        resp = await self.client.request(
            "POST",
            "/submit-job",
            json={"tx": tx_bytes.hex(), "add_parents": True},
        )
        self.assertEqual(200, resp.status)
        data = await resp.json()
        job_id = data["job_id"]

        for _ in range(50):
            await asyncio.sleep(0.1)
            resp = await self.client.request(
                "GET", "/job-status", params={"job-id": job_id}
            )
            data = await resp.json()
            if data["status"] == "done":
                break

        self.assertEqual("done", data["status"])
        self.assertEqual(2, len(data["tx"]["parents"]))


# ---------------------------------------------------------------------------
# TxJob unit tests with shielded outputs
# ---------------------------------------------------------------------------


class TestTxJobShielded(unittest.TestCase):
    """Test that TxJob correctly handles transactions with shielded outputs."""

    __test__ = True

    def test_txjob_parses_shielded_tx(self):
        """TxJob can be created from a transaction with shielded outputs."""
        outputs = [_make_amount_shielded_output(), _make_full_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)

        job = TxJob(tx_bytes)
        tx = job.get_tx()
        self.assertIsInstance(tx, Transaction)
        self.assertTrue(tx.has_shielded_outputs())

    def test_txjob_uuid_includes_shielded_data(self):
        """The job UUID (tx hash) differs between shielded and non-shielded versions."""
        # Non-shielded
        tx_plain = tx_or_block_from_bytes(BASE_TX_DATA)
        tx_plain.timestamp = int(txstratum.time.time())
        tx_plain.update_hash()
        job_plain = TxJob(bytes(tx_plain))

        # Shielded
        outputs = [_make_amount_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)
        job_shielded = TxJob(tx_bytes)

        self.assertNotEqual(job_plain.uuid, job_shielded.uuid)

    def test_txjob_to_dict_shielded(self):
        """to_dict() works correctly for shielded transactions."""
        outputs = [_make_amount_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)

        job = TxJob(tx_bytes)
        d = job.to_dict()
        self.assertIn("job_id", d)
        self.assertIn("status", d)
        self.assertEqual(d["status"], "pending")

    def test_txjob_set_parents_shielded(self):
        """set_parents works and updates the tx hash for shielded transactions."""
        outputs = [_make_amount_shielded_output()]
        tx_bytes = _build_tx_with_shielded_outputs(outputs)

        job = TxJob(tx_bytes)
        old_tx_hash = job.get_tx().hash

        new_parents = [b"\xaa" * 32, b"\xbb" * 32]
        job.set_parents(new_parents)

        # The internal tx hash should change after parents update
        self.assertNotEqual(old_tx_hash, job.get_tx().hash)
        self.assertEqual(job.get_tx().parents, new_parents)
        # The shielded header should still be present
        self.assertTrue(job.get_tx().has_shielded_outputs())
