# Individual Reflection (Reymes)

## 1. What I Implemented

I implemented Part C (Asynchronous Messaging Path) and Part D (Publish/Subscribe System). For Part C, I created a separate TCP sensor port that accepts UPDATE commands from sensor simulators. Sensors connect and send occupancy deltas. For Part D, I implemented the full pub/sub system with subscribe() and unsubscribe() RPC methods.

## 2. A Bug I Fixed

During load testing, I encountered timeout errors where clients would fail to receive responses within the default timeout period. The 2 issues were insufficient thread allocation and the timeout window being too tight for realistic network conditions under stress. I fixed this by increasing thread_pool_size from 8 to 16 in config.json. I also increased the default timeout from 5.0 to 10.0 seconds in rpc_client.py to give requests enough time to complete.


## 3. One Design Change

I used separate TCP connections for RPC, sensors, and events instead of multiplexing everything over one connection. This prevents event delivery from blocking normal RPC requests.
