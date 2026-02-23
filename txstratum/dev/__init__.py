# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# Dev-miner package: lightweight mining for dev/test environments.
#
# This package replaces the production mining stack (stratum server + cpuminer)
# with in-process mining. The motivation is to simplify the integration test
# infrastructure for hathor-wallet-lib, which previously required four Docker
# containers (fullnode + tx-mining-service + cpuminer + test runner) and suffered
# from non-deterministic block timing and wasted CPU on CI.
#
# Architecture (see also the RFC "Simplified Mining for Integration Tests"):
#
#   tx_miner.py    - Solves transaction PoW by brute-forcing nonces. With
#                    --test-mode-tx-weight on the fullnode, weight is ~1, so
#                    the first nonce almost always works.
#
#   block_miner.py - Background async loop that produces blocks at a steady
#                    cadence (configurable via --block-interval). Replaces
#                    cpuminer entirely.
#
#   manager.py     - Drop-in replacement for TxMiningManager. Implements the
#                    same interface consumed by the HTTP API layer, so the API
#                    code doesn't need any changes.
