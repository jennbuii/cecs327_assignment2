Invidiual Reflection (Jennifer Bui)

1. What you implemented?

   I implemented part A and B. I developed the TCP server using a bounded thread pool in server.py and an RPC layer with the server integrated into server.py and the client in rpc_client.py. In addition to those two files, I also implemented the logging, created serverlogs.log, integrated user configs, and created the config.json file.

3. A bug you fixed.

   A bug that I fixed was properly removing a reservation after its time has expired. At first, I was only checking and removing reservations when commands were called and executed. In real world uses, reservations should automatically be removed after its time is up so I created another thread that runs in the background that check and automatically removes expired reservations.

5. One design change.

   I made it so that users can switch between text protocol and RPC by modifying a config option in config.json. This allows me to keep my server implementation for both in one file, allowing me to reuse parts of my code.
