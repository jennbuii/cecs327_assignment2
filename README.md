# cecs327_assignment2

## Build/Run Steps:
First start server.py and then depending on the mode in config.json, proceed with Running Text Protocol or Running RPC Layer below.

### Starting server.py
1. New Terminal (A)
2. python server.py <config.json> [expiration_seconds]

Note: expiration_seconds is an optional argument

### Running Text Protocol (mode = "text" in config.json):

1. New Terminal (B,C,D,...) for as many clients needed
2. ncat \<host> \<port> (ncat installed via nmap.org)
3. Enter commands: [PING, LOTS, AVAIL <lot_id>, RESERVE <lot_id> \<plate>, CANCEL <lot_id> \<plate>]

### Running RPC Layer (mode = "rpc" in config.json):

Starting rpc_client.py
1. New Terminal (B,C,D,...) for as many clients needed
2. python rpc_client.py \<host> \<port> [timeout_seconds]
3. Enter commands: [ping, lots, avail <lot_id>, reserve <lot_id> \<plate>, cancel <lot_id> \<plate>]

Note: timeout_seconds is an optional argument

## RPC Path: Caller → Client Stub → TCP → Server Skeleton → Method → Return → Client Stub → Caller
Caller (repl) -> Client Stub (getLots, getAvailability, reserve, cancel) -> RPCClient.call() -> framing_write() (client) -> TCP -> rpc_handle_conn() -> rpc_dispatcher() -> command() -> framing_write() (server) -> framing_read() (client) -> RPCClient.call() -> Caller

## Framing/Marshalling Specs

**[4-byte big endian unsigned int length][payload bytes]**

The 4-byte unsigned integer, big endian, indicate the size of the message/payload, preventing TCP message sticking. Then the receiver knows where the message stops, allowing them to separate each incoming messages. Payloads are serailized as JSON and encoded using UTF-8 and are sent over the network.

Wire Format: (JSON)
- Request: {rpcId:uint32, method:str, args:any[]}
- Reply: {rpcId, result, error}

## Timeout Policy

Clients enforce per-RPC timeout and will stop waiting after the default of 10 seconds if there is no response from the server. If a timeout occurs, rpcTimeoutError is raised. The [timeout_seconds] are configurable as users can choose to override the default timeout_seconds when starting the terminal.

## Thread Model
**Bounded Thread Pool**

## Configuration
Located in config.json:
- app_port
- thread_pool_size
- expiration_seconds (the amount of time it takes for a reservation to expire)
- lots : {"id", "capacity", "occupied"}
- mode (swtich between text protocol and RPC)

Additional configurations:
- expiration_seconds can also be changed via the terminal when starting server.py
- timeout_seconds (timeout policy) can be changed via the terminal when starting rpc_client.py

## Pub/Sub Design

Uses 3 separate TCP ports (RPC: 1234, Sensor: 1235, Event: 1236) to prevent event delivery from blocking normal requests.

- Clients call `subscribe(lotId)` via RPC to get a `subId`
- Clients connect to event port and send `SUB <subId>` to receive events
- Server pushes `EVENT <lotId> <free> <timestamp>` when occupancy changes
- Each subscriber gets a bounded queue up to 100 events and runs on dedicated threads

## Back-Pressure Policy

**Disconnect on overflow**

Each subscriber has a queue up to 100 events. If the queue fills, the server immediately disconnects that subscriber and logs the event. This prevents slow subscribers from affecting fast ones or consuming excessive memory.

## Python Virtual Environment Setup

1. Create venv
- python -m venv .venv

2. Activate
- macOS/Linux: source .venv/bin/activate
- Windows: .venv\Scripts\activate

3. Install
- pip install -r requirements.txt

4. Run server + clients

On a separate terminal for the server and client, both with venv activated:

- Server: python server.py <config.json> [expiration_seconds]
- Client: python rpc_client.py <host> <port> [timeout_seconds]
