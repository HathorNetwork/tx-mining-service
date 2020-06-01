# Cheatsheet for testing

curl -v "http://127.0.0.1:8080/job-status?job-id=1"

curl -v -X POST --data "abc" "http://127.0.0.1:8080/submit-job"
curl -v -X POST --data "{}" "http://127.0.0.1:8080/submit-job"
curl -v -X POST --data '{"tx": "1234"}' "http://127.0.0.1:8080/submit-job"

# Tx Distribution Among Miners

## Example

Miners: m1, m2, and m3.

New tx:

tx1: m1, m2, m3, m4

New tx:

tx1: m1, m2
tx2: m3, m4

New tx:

tx1: m1
tx2: m3, m4
tx3: m2

- What if all miners always mine the same tx?
