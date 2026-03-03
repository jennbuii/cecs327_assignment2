# cecs327_assignment2

Build/Run Steps:

Starting server.py
1. New Terminal (A)
2. python server.py <config.json> [expiration_seconds]
Note: expiration_seconds is an optional argument

Running Text Protocol (mode = "text" in config.json):

...

Running RPC Layer (mode = "rpc" in config.json):

Starting rpc_client.py
1. New Terminal (B,C,D,...) for as many clients needed
2. python rpc_client.py <host> <port> [timeout_seconds]
Note: timeout_seconds is an optional argument

WIP - need to finish
