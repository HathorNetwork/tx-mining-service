FROM python:3.11-alpine AS build

WORKDIR /code

# Why we need rust: https://github.com/pyca/cryptography/issues/5771
RUN apk add --no-cache gcc musl-dev libffi-dev openssl-dev rust cargo

RUN pip --no-input --no-cache-dir install --upgrade pip wheel
RUN pip --no-input --no-cache-dir install 'poetry>=1.2.0b2'

# Copy hathorlib source (local path dependency: ../hathor-core/hathorlib).
# Build from the parent directory:
#   docker build -f tx-mining-service/Dockerfile -t tx-mining-service .
COPY hathor-core/hathorlib/ /code/hathor-core/hathorlib/

# Install in a subdirectory so the relative path "../hathor-core/hathorlib"
# in pyproject.toml resolves correctly to /code/hathor-core/hathorlib/.
COPY tx-mining-service/poetry.lock tx-mining-service/pyproject.toml /code/tx-mining-service/

WORKDIR /code/tx-mining-service

RUN poetry config virtualenvs.create false \
  && poetry install --only main --no-interaction --no-ansi

FROM python:3.11-alpine

COPY --from=build /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
# hathorlib is installed in develop mode (.pth file points to this path)
COPY --from=build /code/hathor-core/hathorlib /code/hathor-core/hathorlib
RUN apk add libgcc

COPY tx-mining-service/txstratum/ ./txstratum
COPY tx-mining-service/main.py tx-mining-service/log.conf ./

ENTRYPOINT ["python", "-m", "main"]
