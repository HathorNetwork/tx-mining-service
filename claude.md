# tx-mining-service

## Overview

A Python service that mines Hathor blockchain transactions and blocks. It connects users (who submit transactions needing PoW) with miners (who solve the PoW via the Stratum protocol). When no transactions are pending, miners work on block templates from the fullnode.

## Architecture

```
Users ──HTTP──▶ API (aiohttp, port 8035/8080) ──▶ TxMiningManager ──▶ StratumProtocol ──stratum──▶ Miners
                                                         │
                                              fullnode (block templates, tx parents, push)
```

### Key Modules

| File | Responsibility |
|------|----------------|
| `main.py` | Entry point, calls `cli.main()` |
| `txstratum/cli.py` | Argument parsing (ConfigArgParse with `HATHOR_` env prefix), service initialization, signal handling, graceful shutdown |
| `txstratum/api.py` | HTTP API: `POST /submit-job`, `GET /job-status`, `POST /cancel-job`, `GET /health`, `GET /mining-status` |
| `txstratum/manager.py` | `TxMiningManager` — job queue, block template updates, miner assignment, timeout/cleanup scheduling |
| `txstratum/protocol.py` | `StratumProtocol` — Stratum JSON-RPC over TCP, per-miner difficulty adjustment, hashrate estimation, job dispatch |
| `txstratum/jobs.py` | `TxJob` (API-level), `MinerTxJob` / `MinerBlockJob` (miner-level wrappers), `JobStatus` enum |
| `txstratum/filters.py` | Transaction filtering: `FileFilter` (banned txids/addresses), `TOIFilter` (external compliance service) |
| `txstratum/healthcheck/healthcheck.py` | Health checks: miner connectivity, fullnode reachability, job completion times |
| `txstratum/utils.py` | `Periodic` (async task scheduler), `JSONRPCProtocol`, `LineProtocol`, version comparison, expected mining time calculation |
| `txstratum/pubsub.py` | Simple asyncio pub/sub for internal events (tx solved, timeout, miner connected/disconnected) |
| `txstratum/prometheus.py` | Prometheus metrics export (file-based and HTTP-based) |
| `txstratum/middleware.py` | Wallet version checking middleware (desktop, mobile, headless) |
| `txstratum/constants.py` | `DEFAULT_EXPECTED_MINING_TIME` |
| `txstratum/time.py` | Pluggable time function for testing |

### Dev-Miner Mode

When `HATHOR_DEV_MINER=true`, the service runs in lightweight dev/test mode:

- **No stratum server** — transactions are mined synchronously in-process (trivial PoW with weight ~1)
- **Built-in block miner** — background loop polls fullnode for block templates, solves PoW directly, submits blocks at a configurable pace
- **Same HTTP API** — all existing endpoints work identically, making it a drop-in replacement

See `txstratum/dev/` for the dev-miner components.

## Tech Stack

- **Language:** Python 3.9+
- **Async framework:** aiohttp + asyncio
- **Key dependency:** `hathorlib` (transaction/block serialization, hashing, client)
- **Build:** Poetry (`pyproject.toml`), Docker multi-stage (`python:3.9-alpine`)
- **Tests:** pytest + pytest-aiohttp + asynctest

## Configuration

All CLI arguments accept `HATHOR_` prefixed environment variables (via ConfigArgParse).

| Argument | Env Var | Default | Purpose |
|----------|---------|---------|---------|
| `backend` (positional) | `HATHOR_BACKEND` | required | Fullnode API URL |
| `--api-port` | `HATHOR_API_PORT` | 8080 | HTTP API port |
| `--stratum-port` | `HATHOR_STRATUM_PORT` | 8000 | Stratum TCP port |
| `--address` | `HATHOR_ADDRESS` | None | Block reward mining address |
| `--testnet` | `HATHOR_TESTNET` | false | Use testnet config |
| `--tx-timeout` | `HATHOR_TX_TIMEOUT` | 20s | Transaction mining timeout |
| `--max-tx-weight` | `HATHOR_MAX_TX_WEIGHT` | 35.0 | Max tx weight to accept |
| `--dev-miner` | `HATHOR_DEV_MINER` | false | Enable dev-miner mode |
| `--block-interval` | `HATHOR_BLOCK_INTERVAL` | 1000 | Block mining interval in ms (dev-miner mode) |

## Development

```bash
poetry install
python main.py http://localhost:8080 --testnet --address <addr>

# Dev-miner mode (no external miners needed):
HATHOR_DEV_MINER=true python main.py http://fullnode:8080 --testnet --address <addr> --api-port 8035

# Tests:
make tests
# or:
pytest
```

## Docker

```bash
docker build -t tx-mining-service .
docker run -it --network=host tx-mining-service http://fullnode:8080 --address <addr>
```

## Job Lifecycle

`pending` → `getting-parents` (if `add_parents=true`) → `enqueued` → `mining` → `done` | `failed` | `timeout` | `cancelled`

## Important Patterns

- `TxMiningManager.__call__()` is the asyncio protocol factory for new stratum connections
- Block templates are fetched every 3s via `HathorClient.get_block_template()`
- Transaction parents come from `HathorClient.get_tx_parents()`
- The `Periodic` utility class runs async functions on a fixed interval
- `txstratum.time` provides a pluggable time function — tests override it to control timing
- All transaction serialization uses `hathorlib` (`tx_or_block_from_bytes`, `BaseTransaction`, etc.)
