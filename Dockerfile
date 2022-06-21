FROM python:3.9-alpine as build

WORKDIR /code

# Why we need rust: https://github.com/pyca/cryptography/issues/5771
RUN apk add --no-cache gcc musl-dev libffi-dev openssl-dev rust cargo

RUN pip --no-input --no-cache-dir install --upgrade pip wheel
RUN pip --no-input --no-cache-dir install 'poetry==1.2.0b2'

COPY poetry.lock pyproject.toml /code/

RUN poetry config virtualenvs.create false \
  && poetry install --only main --no-interaction --no-ansi

FROM python:3.9-alpine

COPY --from=build /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages
RUN apk add libgcc

COPY txstratum/ ./txstratum
COPY main.py log.conf ./

ENTRYPOINT ["python", "-m", "main"]
