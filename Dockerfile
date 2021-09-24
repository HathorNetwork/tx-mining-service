# set base image (host OS)
FROM python:3.8-slim

# set the working directory in the container
WORKDIR /code

# install poetry
RUN pip install 'poetry==1.1.6'

# copy the dependencies file to the working directory
COPY poetry.lock pyproject.toml /code/

# install dependencies
RUN poetry config virtualenvs.create false \
  && poetry install --no-dev --no-interaction --no-ansi

# copy the content of the local src directory to the working directory
COPY . .
# command to run on container start

ENTRYPOINT ["poetry", "run", "python", "main.py"]
