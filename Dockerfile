FROM python:3.9-alpine AS build

WORKDIR /code

# Why we need rust: https://github.com/pyca/cryptography/issues/5771
RUN apk add --no-cache gcc musl-dev libffi-dev openssl-dev rust cargo

RUN pip --no-input --no-cache-dir install --upgrade pip wheel
RUN pip --no-input --no-cache-dir install 'poetry>=1.2.0b2'

COPY poetry.lock pyproject.toml /code/

RUN poetry config virtualenvs.create false \
  && poetry install --only main --no-interaction --no-ansi

FROM python:3.9-alpine

RUN addgroup -g 10001 appuser && adduser -u 10001 -G appuser -D -h /app appuser

WORKDIR /app

COPY --from=build /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages
RUN apk add libgcc

COPY --chown=appuser:appuser txstratum/ ./txstratum
COPY --chown=appuser:appuser main.py log.conf ./

USER appuser

ENTRYPOINT ["python", "-m", "main"]
