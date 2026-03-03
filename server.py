import threading
import queue
import sys
import json
import time
import struct
from socket import AF_INET, SOCK_STREAM, SOL_SOCKET, SO_REUSEADDR, socket
from datetime import datetime

HOST = "127.0.0.1"
DEFAULT_EXPIRATION_SECONDS = 300
CLEANUP_INTERVAL_SECONDS = 5
DEFAULT_BACKLOG = 100

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
    def __init__(self, expiration_seconds, config_lots={}, sensor_queue_size=1000, subscriber_queue_size=100):
        self.lock = threading.Lock()
        self.expiration_seconds = expiration_seconds
        # lots -> {id, capacity, occupied, free}
        self.lots = config_lots
        self.reservations = {}
        self.update_queue = queue.Queue(maxsize=sensor_queue_size)
        self.subscriber_queue_size = subscriber_queue_size
        self.next_sub_id = 1
        self.subscriptions = {}  # subId -> {lot_id, queue, active, conn}
        self.lot_subscribers = {}  # lotId -> set(subId)

    # Part D: remove a subscription from pub/sub registry and close its event connection
def remove_subscription_locked(state, sub_id):
    sub = state.subscriptions.get(sub_id)
    if not sub:
        return False
    sub["active"] = False
    lot_id = sub["lot_id"]
    if lot_id in state.lot_subscribers:
        state.lot_subscribers[lot_id].discard(sub_id)
        if not state.lot_subscribers[lot_id]:
            del state.lot_subscribers[lot_id]
    conn = sub.get("conn")
    if conn is not None:
        try:
            conn.close()
        except OSError:
            pass
    del state.subscriptions[sub_id]
    return True

# Part D: publish EVENT <lotId> <free> <timestamp> to all subscribers of a lot
def publish_lot_change_locked(state, lot_id):
    lot = state.lots.get(lot_id)
    if lot is None:
        return
    event_message = f"EVENT {lot_id} {lot['free']} {int(time.time())}\n"
    for sub_id in list(state.lot_subscribers.get(lot_id, set())):
        sub = state.subscriptions.get(sub_id)
        if not sub or not sub["active"]:
            remove_subscription_locked(state, sub_id)
            continue
        try:
            sub["queue"].put_nowait(event_message)
        except queue.Full:
            msg = json.dumps({"event_type": "SUBSCRIBER_DROP", "sub_id": sub_id, "lot_id": lot_id, "reason": "queue_overflow"})
            logger(msg)
            remove_subscription_locked(state, sub_id)

# Part D: create a new subscription and allocate a bounded outbound queue
def subscribe_locked(state, lot_id):
    sub_id = state.next_sub_id
    state.next_sub_id += 1
    state.subscriptions[sub_id] = {
        "lot_id": lot_id,
        "queue": queue.Queue(maxsize=state.subscriber_queue_size),
        "active": True,
        "conn": None,
    }
    state.lot_subscribers.setdefault(lot_id, set()).add(sub_id)
    return sub_id

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
            changed_lots = cleanup(state) # clean up expired reservations
            for lot_id in changed_lots:
                publish_lot_change_locked(state, lot_id)
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
            changed_lots = cleanup(state) # clean up expired reservations
            for lot_id in changed_lots:
                publish_lot_change_locked(state, lot_id)
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
            changed_lots = cleanup(state) # clean up expired reservations
            for expired_lot_id in changed_lots:
                publish_lot_change_locked(state, expired_lot_id)
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
            publish_lot_change_locked(state, lot_id)
            msg = json.dumps({"event_type": "RESERVE", "lot_id": lot_id, "plate": plate})
            logger(msg)
            return "OK\n"
    # CANCEL <lot_id> <plate> - cancels a reservation, returns OK or NOT_FOUND
    elif cmd_name == "CANCEL": 
        with state.lock:
            changed_lots = cleanup(state) # clean up expired reservations
            for expired_lot_id in changed_lots:
                publish_lot_change_locked(state, expired_lot_id)
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
            publish_lot_change_locked(state, lot_id)
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
    # Part D1: pub/sub API method subscribe(lotId) -> subId
    elif method == "subscribe":
        if len(args) < 1:
            reply["error"] = "ERROR missing lot_id"
        else:
            lot_id = args[0]
            with state.lock:
                if lot_id not in state.lots:
                    reply["error"] = "ERROR invalid lot_id"
                else:
                    sub_id = subscribe_locked(state, lot_id)
                    reply["result"] = sub_id
                    msg = json.dumps({"event_type": "SUBSCRIBE", "lot_id": lot_id, "sub_id": sub_id})
                    logger(msg)
    # Part D1: pub/sub API method unsubscribe(subId) -> bool
    elif method == "unsubscribe":
        if len(args) < 1:
            reply["error"] = "ERROR missing sub_id"
        else:
            try:
                sub_id = int(args[0])
            except (TypeError, ValueError):
                reply["error"] = "ERROR invalid sub_id"
                return reply
            with state.lock:
                ok = remove_subscription_locked(state, sub_id)
            reply["result"] = ok
            if not ok:
                reply["error"] = "NOT_FOUND"
            else:
                msg = json.dumps({"event_type": "UNSUBSCRIBE", "sub_id": sub_id})
                logger(msg)
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

def sensor_handle_conn(conn, addr, state):
    # Part C: asynchronous sensor ingress endpoint: UPDATE <lotId> <delta>
    fin = conn.makefile("rb")
    fout = conn.makefile("wb")
    while True:
        line = fin.readline()
        if not line:
            break
        cmd = line.decode("utf-8").strip()
        parts = cmd.split()
        if len(parts) != 3 or parts[0].upper() != "UPDATE":
            fout.write(b"ERROR invalid command syntax\n")
            fout.flush()
            continue
        lot_id = parts[1]
        try:
            delta = int(parts[2])
        except ValueError:
            fout.write(b"ERROR invalid delta\n")
            fout.flush()
            continue
        try:
            state.update_queue.put_nowait((lot_id, delta))
            fout.write(b"QUEUED\n")
        except queue.Full:
            fout.write(b"BUSY\n")
        fout.flush()
    fin.close()
    fout.close()
    conn.close()

def event_handle_conn(conn, addr, state):
    # Part D3: dedicated event delivery connection: SUB <subId> then stream EVENT lines
    fin = conn.makefile("rb")
    fout = conn.makefile("wb")
    line = fin.readline()
    if not line:
        fin.close()
        fout.close()
        conn.close()
        return
    parts = line.decode("utf-8").strip().split()
    if len(parts) != 2 or parts[0].upper() != "SUB":
        fout.write(b"ERROR expected: SUB <subId>\n")
        fout.flush()
        fin.close()
        fout.close()
        conn.close()
        return
    try:
        sub_id = int(parts[1])
    except ValueError:
        fout.write(b"ERROR invalid subId\n")
        fout.flush()
        fin.close()
        fout.close()
        conn.close()
        return

    with state.lock:
        sub = state.subscriptions.get(sub_id)
        if not sub or not sub["active"]:
            fout.write(b"ERROR invalid subId\n")
            fout.flush()
            fin.close()
            fout.close()
            conn.close()
            return
        if sub["conn"] is not None:
            fout.write(b"ERROR subId already connected\n")
            fout.flush()
            fin.close()
            fout.close()
            conn.close()
            return
        sub["conn"] = conn
        outbound_queue = sub["queue"]

    fout.write(b"OK\n")
    fout.flush()
    try:
        while True:
            with state.lock:
                sub = state.subscriptions.get(sub_id)
                if not sub or not sub["active"]:
                    break
            try:
                event_line = outbound_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            fout.write(event_line.encode("utf-8"))
            fout.flush()
    except (ConnectionError, OSError):
        pass
    finally:
        with state.lock:
            sub = state.subscriptions.get(sub_id)
            if sub and sub.get("conn") is conn:
                sub["conn"] = None
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

def accept_conn(listen_port, worker_queue, backlog): # accepts incoming connections and puts them in the worker queue
    s = socket(AF_INET, SOCK_STREAM)
    s.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    s.bind((HOST, listen_port))
    s.listen(backlog)
    while True:
        conn, addr = s.accept()
        worker_queue.put((conn, addr))
    
def cleanup(state): # removes expired reservations from the state
    current_time = time.time()
    expired = []
    changed_lots = set()
    for (lot_id, plate), expiration in state.reservations.items():
        if expiration < current_time:
            expired.append((lot_id, plate))
    for lot_id, plate in expired:
        del state.reservations[(lot_id, plate)]
        state.lots[lot_id]["occupied"] -= 1
        state.lots[lot_id]["free"] += 1
        changed_lots.add(lot_id)
    return changed_lots

def cleanup_reservations(state, expiration_event): # background thread function, cleans up expired reservations
    while not expiration_event.is_set():
        with state.lock:
            changed_lots = cleanup(state)
            for lot_id in changed_lots:
                publish_lot_change_locked(state, lot_id)
        time.sleep(CLEANUP_INTERVAL_SECONDS)

def apply_sensor_update(state, lot_id, delta):
    # Part C: apply queued sensor updates in worker threads and trigger pub/sub fan-out
    with state.lock:
        if lot_id not in state.lots:
            return False
        lot = state.lots[lot_id]
        old_free = lot["free"]
        new_occupied = lot["occupied"] + delta
        if new_occupied < 0:
            new_occupied = 0
        if new_occupied > lot["capacity"]:
            new_occupied = lot["capacity"]
        lot["occupied"] = new_occupied
        lot["free"] = lot["capacity"] - new_occupied
        if lot["free"] != old_free:
            publish_lot_change_locked(state, lot_id)
        msg = json.dumps({"event_type": "UPDATE", "lot_id": lot_id, "delta": delta})
        logger(msg)
        return True

def sensor_update_worker(state, stop_event):
    # Part C: decoupled worker path so sensor traffic does not block RPC request/response
    while not stop_event.is_set():
        try:
            lot_id, delta = state.update_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            apply_sensor_update(state, lot_id, delta)
        finally:
            state.update_queue.task_done()

def main():
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Usage: python server.py <config.json> [expiration_seconds]")
        return
    
    config_file = sys.argv[1]
    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    app_port = int(config["app_port"])
    sensor_port = int(config.get("sensor_port", app_port + 1))
    event_port = int(config.get("event_port", app_port + 2))
    thread_pool_size = config.get("thread_pool_size")
    sensor_worker_threads = int(config.get("sensor_worker_threads", 2))
    event_worker_threads = int(config.get("event_worker_threads", 4))
    sensor_queue_size = int(config.get("sensor_queue_size", 1000))
    subscriber_queue_size = int(config.get("subscriber_queue_size", 100))
    backlog = int(config.get("backlog", DEFAULT_BACKLOG))
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
    state = ParkingLot(expiration_seconds, config_lots, sensor_queue_size, subscriber_queue_size)
    expiration_event = threading.Event()
    cleanup_thread = threading.Thread(target=cleanup_reservations, args=(state, expiration_event), daemon=True)
    cleanup_thread.start()

    sensor_stop_event = threading.Event()
    for _ in range(sensor_worker_threads):
        thread = threading.Thread(target=sensor_update_worker, args=(state, sensor_stop_event), daemon=True)
        thread.start()

    worker_queue = queue.Queue(maxsize=backlog * 4)
    for id in range(thread_pool_size): 
        thread = threading.Thread(target=work, args=(id, worker_queue, state, handler), daemon=True)
        thread.start()

    sensor_worker_queue = queue.Queue(maxsize=backlog * 4)
    for id in range(sensor_worker_threads):
        thread = threading.Thread(target=work, args=(id, sensor_worker_queue, state, sensor_handle_conn), daemon=True)
        thread.start()

    event_worker_queue = queue.Queue(maxsize=backlog * 4)
    for id in range(event_worker_threads):
        thread = threading.Thread(target=work, args=(id, event_worker_queue, state, event_handle_conn), daemon=True)
        thread.start()

    app_acceptor = threading.Thread(target=accept_conn, args=(app_port, worker_queue, backlog), daemon=True)
    # Part C: separate listener for sensor async updates
    sensor_acceptor = threading.Thread(target=accept_conn, args=(sensor_port, sensor_worker_queue, backlog), daemon=True)
    # Part D3: separate listener for pub/sub event stream delivery
    event_acceptor = threading.Thread(target=accept_conn, args=(event_port, event_worker_queue, backlog), daemon=True)

    app_acceptor.start()
    sensor_acceptor.start()
    event_acceptor.start()

    print(f"RPC/Text port: {app_port}, Sensor port: {sensor_port}, Event port: {event_port}")

    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()