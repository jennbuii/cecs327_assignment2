import sys
import json
import struct
from socket import AF_INET, SOCK_STREAM, socket
import socket

def framing_read(conn): # reads a framed message from the connection [4 byte length header followed by message bytes]
    try:
        header = b""
        while len(header) < 4:
            data = conn.recv(4 - len(header))
            if not data:
                return None
            header += data
        message_length = struct.unpack("!I", header)[0]
        message = b""
        while len(message) < message_length:
            data = conn.recv(message_length - len(message))
            if not data:
                return None
            message += data
        return message
    except ConnectionResetError:
        return None

def framing_write(conn, message): # writes a framed message to the connection [4 byte length header followed by message bytes]
    try:
        conn.sendall(struct.pack("!I", len(message)) + message)
    except ConnectionResetError:
        raise ConnectionError("Connection closed")

# raised when an RPC call times out (no reply received within timeout period)
class rpcTimeoutError(Exception):
    pass

class RPCClient:
    def __init__(self, host, port, timeout=5.0):
        self.sock = socket.create_connection((host, port))
        self.sock.settimeout(timeout)
        self.next_rpc_id = 1

    def call(self, method, args):
        rpc_id = self.next_rpc_id
        self.next_rpc_id += 1
        req = {"rpcId": rpc_id, "method": method, "args": args}
        framing_write(self.sock, json.dumps(req).encode("utf-8"))
        try:
            reply_message = framing_read(self.sock)
        except socket.timeout:
            raise rpcTimeoutError("RPC timed out")
        if reply_message is None:
            raise ConnectionError("Connection closed")
        reply = json.loads(reply_message.decode("utf-8"))
        return reply

    def close(self):
        self.sock.close()

    # stubs for RPC methods
    def getLots(self):
        return self.call("getLots", []).get("result")
    
    def getAvailability(self, lot_id):
        reply = self.call("getAvailability", [lot_id])
        return reply.get("result"), reply.get("error")
    
    def reserve(self, lot_id, plate):
        reply = self.call("reserve", [lot_id, plate])
        return reply.get("result"), reply.get("error")
    
    def cancel(self, lot_id, plate):
        reply = self.call("cancel", [lot_id, plate])
        return reply.get("result"), reply.get("error")

    def subscribe(self, lot_id):
        reply = self.call("subscribe", [lot_id])
        return reply.get("result"), reply.get("error")

    def unsubscribe(self, sub_id):
        reply = self.call("unsubscribe", [sub_id])
        return reply.get("result"), reply.get("error")
            
def main():
    if len(sys.argv) < 3 or len(sys.argv) > 4:
        print("Usage: python rpc_client.py <host> <port> [timeout_seconds]")
        return
    host = sys.argv[1]
    port = int(sys.argv[2])
    if len(sys.argv) == 4:
        timeout = float(sys.argv[3])
    else:        
        timeout = 5.0
    client = RPCClient(host, port, timeout)

    # command-line interface for RPC client
    try:
        while True:
            try: 
                user_input = input("Enter command: ")
                input_parts = user_input.strip().split()
                if not input_parts:
                    continue
                cmd = input_parts[0]
                if cmd == "lots":
                    print(client.getLots())
                elif cmd == "avail" and len(input_parts) == 2:
                    lot_id = input_parts[1]
                    result, error = client.getAvailability(lot_id)
                    if result:
                        print(result)
                    else:
                        print(error)
                elif cmd == "reserve" and len(input_parts) == 3:
                    lot_id = input_parts[1]
                    plate = input_parts[2]
                    result, error = client.reserve(lot_id, plate)
                    if result:
                        print(result)
                    else:
                        print(error)
                elif cmd == "cancel" and len(input_parts) == 3:
                    lot_id = input_parts[1]
                    plate = input_parts[2]
                    result, error = client.cancel(lot_id, plate)
                    if result:
                        print(result)
                    else:
                        print(error)
                elif cmd == "subscribe" and len(input_parts) == 2:
                    lot_id = input_parts[1]
                    result, error = client.subscribe(lot_id)
                    if result is not None:
                        print(f"subId={result}")
                    else:
                        print(error)
                elif cmd == "unsubscribe" and len(input_parts) == 2:
                    sub_id = input_parts[1]
                    result, error = client.unsubscribe(sub_id)
                    if result:
                        print(result)
                    else:
                        print(error)
                elif cmd == "exit":
                    break
                else:
                    print("ERROR Invalid command")
            except rpcTimeoutError as e:
                print(f"ERROR {e}")
                break
            except ConnectionError as e:
                print(f"ERROR {e}")
                break
    finally:
        client.close()

if __name__ == "__main__":
    main()