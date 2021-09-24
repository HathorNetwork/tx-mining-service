# tx-mining-service

The tx-mining-service is a service used to resolving transactions before propagating them into Hathor's mainnet.

It has two kinds of clients: users and miners. Users use it when sending new transactions; while miners do the work of resolving the transactions.

When there is no transaction to be mined, miners receive mining blocks to be mined.


## API Docs

Check out the full documentation in the OpenAPI Documentation in `api-docs.js`.


## How to run?

To install dependencies, run `poetry install`.

    python main.py https://node1.mainnet.hathor.network/

Or you can use the Docker image

    docker build -t tx-mining-service .
    docker run -it --network="host" tx-mining-service <full-node-address:port> --address <mining-reward-address> --api-port 8080 --stratum-port 8000


## How to test?

    make tests
