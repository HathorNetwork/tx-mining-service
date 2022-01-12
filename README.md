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
- cli.py: it's the entrypoint of the service. It parses the command line arguments and starts the other modules.
- api.py: the API server
- protocol.py: Communicates with its miner through the Stratum protocol, to send jobs and receive submissions. Adjusts the jobs difficulty. Calculates the miner hashrate.
- manager.py: Manages the job queue. Updates the block template. Assigns jobs to miners. Controls job timeouts. Adds parents to transactions.

Those are the most important events that happen in the system:
- A new miner connects to the stratum server
- A new job is submitted to the API server
- A miner submits a job solution to the stratum server
- A new block template is found

We will talk more about each event in the next sections.

### A new miner connects
When a new miner connects, a new instance of the protocol module is created for it and registered in the manager.

It starts 2 periodic tasks:

- Estimator Task: It will adjust the difficulty of the next job to be mined. It will do so with the goal of keeping the time to solve a job equal to the TARGET_JOB_TIME.

This difficulty adjustment is done to allow us to calculate the hashrate of the miner, by checking how many jobs it submitted in a given period and their weight.

It should be noted that because of this mechanism, the difficulty we assign to jobs can be lower than the difficulty of the block template, in the case of block jobs. Especially in the mainnet, where the difficulty of the network is too high.

This will make the miners sometimes send submissions that are valid for this lower difficulty, but not for the real difficulty we need to solve the block.

That's why we need to verify those submissions and reject them if they are not valid. In this case, we will just send a new job to the miner.

For transaction jobs, this will hardly ever happen, since their difficulty is usually low.

This task runs every 15 seconds.

- Job Update Task: It will update the job this miner is working on. The manager will send a MinerTxJob, if there is one, and force the miner to work immediately on it. Otherwise, it will just send a MinerBlockJob, and do not force the miner to work immediately on it.

This task is important to make sure we prioritize tx jobs over block jobs.

This task runs every 2 seconds.

### A new block template is found
The manager runs a periodic task every 3 seconds to check if there is a new block template.

If a new one is found, it will try to update the current job of all miners, but only if they are currently mining a block.
If they are mining a tx, it will do nothing.

### A new job is submitted to the API server

When a new job arrives at the API, it will be added to the manager's job queue.

A timeout is scheduled for it. If the timeout is reached, the job will be removed from the queue and immediately stopped being worked in the miners.

If the queue was empty, the manager will call an update on all miners jobs, to make them work on the new tx job.

All miners will be working in the same tx job at the same time.

### A job is submitted to the stratum server

If the submitted job is a block job, it will be propagated to the network, and the manager will trigger an update in the block template so that a new block begins mining.

If the submitted job is a tx job, it will be first removed from the queue, then we will instruct all miners that were mining it to stop and get a new job (which could be another tx job, if there is still a tx job in the queue, or a block job).