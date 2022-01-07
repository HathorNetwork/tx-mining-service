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

We also have a public Docker image if you don't want to build it yourself:
    docker run -it --network="host" hathornetwork/tx-mining-service <full-node-address:port> --address <mining-reward-address> --api-port 8080 --stratum-port 8000


## How to test?

    make tests

## How it works

The main components in the code are:
- api.py: the API server
- protocol.py: handles communication with a specific miner through stratum protocol
- manager.py: manages the job queue and assigns jobs to miners

When the server starts, those are the first things to happen:
-  

There are some important events that happen in the system:
- A new miner connects to the stratum server
- A new job is submitted to the API server
- A miner submits a job solution to the stratum server
- A new block template is found

We will talk more about them in the next sections.

### A new miner connects
When a new miner connects, a new instance of the protocol module is created for it and registered in the manager.

It starts 2 periodic tasks:

Estimator Task: It will adjust the difficulty of the next block (or tx?) to be mined. It will do so by summing the weight of all jobs submitted in the last 15 minutes (by default), and use this to set the next difficulty, increasing or decreasing it if necessary. (TODO: is this difficulty is used both by block mining and by tx mining?)

This task runs every 15 seconds.

Every time the difficulty is changed, the miner job is updated. (TODO Why?)

- Job Update Task: It will update the job this miner is working on. The manager will send a MinerTxJob, if there is one, and force the miner to work immediately on it. Otherwise, it will just send a MinerBlockJob.

This task runs every 2 seconds.

### A new block template is found
The manager runs a periodic task every 3 seconds to check if there is a new block template.

If a new one is found, it will try to update the current job of all miners, but only if they are currently mining a block.
If they are mining a tx, it will do nothing.

### A new job is submitted to the API server

When a new job arrives at the API, it will be added to the manager's job queue.

A timeout is scheduled for it. If the timeout is reached, the job will be removed from the queue and immediately stopped being processed in the miners.

If the queue was empty, the manager will call an update on all miners jobs, to make them work on the new tx job.

All miners will be working in the same tx job at the same time.

### A job is submitted to the stratum server

If the submitted job is a block job, it will be propagated to the network, and the manager will trigger an update in the block template so that a new block begins mining.

If the submitted job is a tx job, it will be first removed from the queue, then we will instruct all miners that were mining it to stop and get a new job (which could be another tx job, if there is still a tx job in the queue, or a block job).