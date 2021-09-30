FROM python:3.8-slim as build

WORKDIR /code

RUN pip install --no-cache-dir 'poetry==1.1.6'

COPY poetry.lock pyproject.toml /code/

RUN poetry config virtualenvs.create false \
  && poetry install --no-dev --no-interaction --no-ansi

FROM python:3.8-alpine

COPY --from=build /usr/local/lib/python3.8/site-packages /usr/local/lib/python3.8/site-packages

COPY txstratum/ ./txstratum
COPY main.py log.conf ./

ENTRYPOINT ["python", "-m", "main"]
