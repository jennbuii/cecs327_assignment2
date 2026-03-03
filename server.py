import threading
import queue
import sys
import json
import time
import struct
from socket import AF_INET, SOCK_STREAM, socket
from datetime import datetime

HOST = "127.0.0.1"
DEFAULT_EXPIRATION_SECONDS = 300
CLEANUP_INTERVAL_SECONDS = 5

def logger(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("serverlogs.log", "a", encoding="utf-8") as f:
        f.write(f"{timestamp} {msg}\n")

def read_lots(config):
    lots = {}
    for lot in config.get("lots", []):
        lot_id = lot["id"]
        capacity = lot["capacity"]
        occupied = lot["occupied"]
        free = capacity - occupied
        lots[lot_id] = {"capacity": capacity, "occupied": occupied, "free": free}
    return lots

class ParkingLot:
    def __init__(self, expiration_seconds, config_lots={}):
        self.lock = threading.Lock()
        self.expiration_seconds = expiration_seconds
        # lots -> {id, capacity, occupied, free}
        self.lots = config_lots
        self.reservations = {} 

def framing_read(conn): #reads a framed message from the connection [4 byte length header followed by message bytes]
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

def framing_write(conn, message):
    conn.sendall(struct.pack("!I", len(message)) + message)

def command(cmd, state): #handles commands from clients, returns response string
    if not cmd:
        return "ERROR invalid command syntax\n"
    cmd_parts = cmd.strip().split()
    cmd_name = cmd_parts[0].upper()

    if cmd_name == "LOTS": # returns JSON list of lots
        with state.lock:
            cleanup(state) # clean up expired reservations
            lots_info = []
            for lot_id, lot in state.lots.items():
                lots_info.append({
                    "id": lot_id,
                    "capacity": lot["capacity"],
                    "occupied": lot["occupied"],
                    "free": lot["free"]
                })
            msg = json.dumps({"event_type": "LOTS"})
            logger(msg)
            return json.dumps(lots_info) + "\n"
    # AVAIL <lot_id> - returns integer number of available spaces in the lot
    elif cmd_name == "AVAIL":
        with state.lock:
            cleanup(state) # clean up expired reservations
            if len(cmd_parts) < 2:
                return "ERROR invalid command syntax\n"
            lot_id = cmd_parts[1]
            if lot_id not in state.lots:
                return "ERROR invalid lot_id\n"
            msg = json.dumps({"event_type": "AVAIL", "lot_id": lot_id})
            logger(msg)
            return str(state.lots[lot_id]["free"]) + "\n"
    # RESERVE <lot_id> <plate> - reserves a space, returns OK, FULL, or EXISTS
    elif cmd_name == "RESERVE":
        with state.lock:
            cleanup(state) # clean up expired reservations
            if len(cmd_parts) < 3:
                return "ERROR invalid command syntax\n"
            lot_id = cmd_parts[1]
            plate = cmd_parts[2]
            if lot_id not in state.lots:
                return "ERROR invalid lot_id\n"
            if (lot_id, plate) in state.reservations:
                return "EXISTS\n"
            if state.lots[lot_id]["free"] <= 0:
                return "FULL\n"
            expiration = time.time() + state.expiration_seconds
            state.reservations[(lot_id, plate)] = expiration
            state.lots[lot_id]["occupied"] += 1
            state.lots[lot_id]["free"] -= 1
            msg = json.dumps({"event_type": "RESERVE", "lot_id": lot_id, "plate": plate})
            logger(msg)
            return "OK\n"
    # CANCEL <lot_id> <plate> - cancels a reservation, returns OK or NOT_FOUND
    elif cmd_name == "CANCEL": 
        with state.lock:
            cleanup(state) # clean up expired reservations
            if len(cmd_parts) < 3:
                return "ERROR invalid command syntax\n"
            lot_id = cmd_parts[1]
            plate = cmd_parts[2]
            if lot_id not in state.lots:
                return "ERROR invalid lot_id\n"
            if (lot_id, plate) not in state.reservations:
                return "NOT_FOUND\n"
            del state.reservations[(lot_id, plate)]
            state.lots[lot_id]["occupied"] -= 1
            state.lots[lot_id]["free"] += 1
            msg = json.dumps({"event_type": "CANCEL", "lot_id": lot_id, "plate": plate})
            logger(msg)
            return "OK\n"
    elif cmd_name == "PING":
        msg = "PING"
        logger(msg)
        return "PONG\n"
    return "ERROR invalid command syntax\n"

# RPC dispatcher and connection handler for RPC mode
# RPC skeleton
def rpc_dispatcher(req, state):
    # request: {"rpcId": uint32, "method": str, "args": any[]}
    # reply: {rpcId, result, error}
    rpcId = req.get("rpcId")
    method = req.get("method")
    args = req.get("args", [])

    reply = {"rpcId": rpcId, "result": None, "error": None}
    if method == "getLots":
        result = command("LOTS", state).strip()
        reply["result"] = json.loads(result)
    elif method == "getAvailability":
        if len(args) < 1:
            reply["error"] = "ERROR missing lot_id"
        else:
            lot_id = args[0]
            result = command(f"AVAIL {lot_id}", state).strip()
            if result.startswith("ERROR"):
                reply["error"] = result
            else:
                reply["result"] = int(result)
    elif method == "reserve":
        if len(args) < 2:
            reply["error"] = "ERROR missing lot_id, plate"
        else:
            lot_id = args[0]
            plate = args[1]
            result = command(f"RESERVE {lot_id} {plate}", state).strip()
            if result  == "OK":
                reply["result"] = True
            else:
                reply["result"] = False
                reply["error"] = result
    elif method == "cancel":
        if len(args) < 2:
            reply["error"] = "ERROR missing lot_id, plate"
        else:
            lot_id = args[0]
            plate = args[1]
            result = command(f"CANCEL {lot_id} {plate}", state).strip()
            if result == "OK":
                reply["result"] = True
            else:
                reply["result"] = False
                reply["error"] = result
    else:
        reply["error"] = "ERROR invalid method"
    return reply

# RPC skeleton connection handler
def rpc_handle_conn(conn, addr, state):
    try:
        while True:
            message = framing_read(conn)
            if message is None:
                break
            req = json.loads(message.decode("utf-8"))
            reply = rpc_dispatcher(req, state)
            reply_message = json.dumps(reply).encode("utf-8")
            framing_write(conn, reply_message)
    except ConnectionError:
        pass
    conn.close()

def handle_conn(conn, addr, state): #handles a single client connection
    fin = conn.makefile("rb")
    fout = conn.makefile("wb")

    while True:
        line = fin.readline()
        if not line:
            break
        cmd = line.decode("utf-8").strip()
        response = command(cmd, state)
        fout.write(response.encode("utf-8"))
        fout.flush()
    fin.close()
    fout.close()
    conn.close()

def work(id, worker_queue, state, handler): # worker thread function, processes tasks from the worker queue
    while True:
        conn, addr = worker_queue.get()
        try:
            handler(conn, addr, state) # rpc_handle_conn or handle_conn depending on mode
        finally:
            worker_queue.task_done()

def accept_conn(app_port, worker_queue): # accepts incoming connections and puts them in the worker queue
    s = socket(AF_INET, SOCK_STREAM)
    s.bind((HOST, app_port))
    s.listen(100)
    while True:
        conn, addr = s.accept()
        worker_queue.put((conn, addr))
    
def cleanup(state): # removes expired reservations from the state
    current_time = time.time()
    expired = []
    for (lot_id, plate), expiration in state.reservations.items():
        if expiration < current_time:
            expired.append((lot_id, plate))
    for lot_id, plate in expired:
        del state.reservations[(lot_id, plate)]
        state.lots[lot_id]["occupied"] -= 1
        state.lots[lot_id]["free"] += 1

def cleanup_reservations(state, expiration_event): # background thread function, cleans up expired reservations
    while not expiration_event.is_set():
        with state.lock:
            cleanup(state)
        time.sleep(CLEANUP_INTERVAL_SECONDS)

def main():
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Usage: python server.py <config.json> [expiration_seconds]")
        return
    
    config_file = sys.argv[1]
    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    app_port = int(config["app_port"])
    thread_pool_size = config.get("thread_pool_size")
    expiration_seconds = int(config.get("expiration_seconds", DEFAULT_EXPIRATION_SECONDS))
    if len(sys.argv) == 3:
        expiration_seconds = int(sys.argv[2])
    mode = config.get("mode", "rpc")
    if mode == "rpc":
        handler = rpc_handle_conn
    elif mode == "text":
        handler = handle_conn
    config_lots = read_lots(config)
    
    print("Starting Parking Server...")
    state = ParkingLot(expiration_seconds, config_lots)
    expiration_event = threading.Event()
    cleanup_thread = threading.Thread(target=cleanup_reservations, args=(state, expiration_event), daemon=True)
    cleanup_thread.start()

    worker_queue = queue.Queue()
    for id in range(thread_pool_size): 
        thread = threading.Thread(target=work, args=(id, worker_queue, state, handler), daemon=True)
        thread.start()

    accept_conn(app_port, worker_queue)

if __name__ == "__main__":
    main()