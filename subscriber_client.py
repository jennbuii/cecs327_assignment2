import socket
import sys

from rpc_client import RPCClient, rpcTimeoutError


def main():
    # Part D:subscribes via RPC, then listens on event stream connection
    if len(sys.argv) < 5 or len(sys.argv) > 6:
        print("Usage: python subscriber_client.py <host> <rpc_port> <event_port> <lot_id> [timeout_seconds]")
        return

    host = sys.argv[1]
    rpc_port = int(sys.argv[2])
    event_port = int(sys.argv[3])
    lot_id = sys.argv[4]
    timeout = float(sys.argv[5]) if len(sys.argv) == 6 else 5.0

    rpc = RPCClient(host, rpc_port, timeout)
    sub_id = None
    event_sock = None

    try:
        sub_id, error = rpc.subscribe(lot_id)
        if sub_id is None:
            print(f"subscribe failed: {error}")
            return

        print(f"subscribed: lot={lot_id} subId={sub_id}")

        event_sock = socket.create_connection((host, event_port))
        event_sock.sendall(f"SUB {sub_id}\n".encode("utf-8"))
        event_stream = event_sock.makefile("rb")

        ack = event_stream.readline().decode("utf-8").strip()
        if ack != "OK":
            print(f"event connect failed: {ack}")
            return

        print("listening for events (Ctrl+C to stop)")
        while True:
            line = event_stream.readline()
            if not line:
                print("event stream closed")
                break
            print(line.decode("utf-8").strip())

    except (ConnectionError, rpcTimeoutError) as error:
        print(f"ERROR {error}")
    except KeyboardInterrupt:
        print("stopping subscriber")
    finally:
        if sub_id is not None:
            try:
                rpc.unsubscribe(sub_id)
            except Exception:
                pass
        rpc.close()
        if event_sock:
            event_sock.close()


if __name__ == "__main__":
    main()
