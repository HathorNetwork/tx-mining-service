# Building and publishing the `shielded-outputs` Docker image

This is an **experimental** build that depends on an unreleased hathorlib carrying
shielded-output support. It is not produced by CI — build and push it manually
from this repo. The official CI at `.github/workflows/docker.yml` is unchanged
and still publishes the regular `hathornetwork/tx-mining-service` tags.

Target image: `hathornetwork/tx-mining-service:shielded-outputs-v1`

## Prerequisites

1. hathorlib checkout at the sibling path `../hathor-core-4/hathorlib`, matching
   the `hathorlib = { path = "../hathor-core-4/hathorlib", develop = true }`
   entry in `pyproject.toml`.
2. That hathorlib must include **both**:
   - `hathorlib.headers.ShieldedOutputsHeader`
   - `hathorlib.headers.UnshieldBalanceHeader` (`VertexHeaderId.UNSHIELD_BALANCE_HEADER = b'\x13'`),
     registered in `hathorlib.vertex_parser.VertexParser.get_supported_headers()`.
3. `docker login` with push access to the `hathornetwork` Docker Hub org.

Quick check from the host before building:

```
python -c "from hathorlib.headers import ShieldedOutputsHeader, UnshieldBalanceHeader"
```

## Build

Build context is the **parent directory** (not this repo), because the
Dockerfile copies `hathor-core-4/hathorlib/` from the sibling path:

```
cd /path/to/Hathor                 # parent of tx-mining-service and hathor-core-4
docker build \
  -f tx-mining-service/Dockerfile \
  -t tx-mining-service:shielded-outputs-v1 \
  .
```

## Verify

Confirm both headers are importable and registered in the parser before
publishing:

```
docker run --rm --entrypoint python tx-mining-service:shielded-outputs-v1 -c "
from hathorlib.headers import ShieldedOutputsHeader, UnshieldBalanceHeader, VertexHeaderId
from hathorlib.vertex_parser import VertexParser
h = VertexParser.get_supported_headers()
assert h[VertexHeaderId.SHIELDED_OUTPUTS_HEADER] is ShieldedOutputsHeader
assert h[VertexHeaderId.UNSHIELD_BALANCE_HEADER] is UnshieldBalanceHeader
print('ok')
"
```

## Publish

```
docker tag  tx-mining-service:shielded-outputs-v1 \
            hathornetwork/tx-mining-service:shielded-outputs-v1
docker push hathornetwork/tx-mining-service:shielded-outputs-v1
```

Record the pushed digest (`sha256:...`) from the final line of `docker push`
output alongside the hathor-core-4 commit the hathorlib was built from, so the
image is reproducible.

## Bumping the tag

When publishing a new experimental cut, increment the numeric suffix
(`shielded-outputs-v2`, `-v3`, ...) rather than overwriting an existing tag —
consumers may be pinned to the old digest.
