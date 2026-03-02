import threading
import queue
import sys
import json
import time
from socket import AF_INET, SOCK_STREAM, socket
from datetime import datetime

HOST = "127.0.0.1"
DEFAULT_EXPIRATION_SECONDS = 300
CLEANUP_INTERVAL_SECONDS = 5

class ParkingLot:
    def __init__(self, expiration_seconds):
        self.lock = threading.Lock()
        self.expiration_seconds = expiration_seconds
        # lots -> {id, capacity, occupied, free}
        self.lots = {
            "G1": {"capacity": 100, "occupied": 26, "free": 74},
            "G2": {"capacity": 150, "occupied": 45, "free": 105},
            "G3": {"capacity": 200, "occupied": 120, "free": 80},
            "G4": {"capacity": 50, "occupied": 10, "free": 40}
        }
        self.reservations = {} # (lot_id, plate) -> timestamp

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
            return "OK\n"
    elif cmd_name == "PING":
        return "PONG\n"
    return "ERROR invalid command syntax\n"

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

def work(id, worker_queue, state): # worker thread function, processes tasks from the worker queue
    while True:
        conn, addr = worker_queue.get()
        handle_conn(conn, addr, state)

def accept_conn(app_port, worker_queue): # accepts incoming connections and puts them in the worker queue
    s = socket(AF_INET, SOCK_STREAM)
    s.bind((HOST, app_port))
    s.listen(100)
    while True:
        conn, addr = s.accept()
        worker_queue.put((conn, addr))
    
def cleanup(state):
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
    if len(sys.argv) <= 2 or  len(sys.argv) >= 3:
        print("Usage: python parking_server.py <app_port> [expiration_seconds]")
        return
    app_port = int(sys.argv[1])
    expiration_seconds = DEFAULT_EXPIRATION_SECONDS
    if len(sys.argv) == 3:
        expiration_seconds = int(sys.argv[2])
    
    print("Starting Parking Server...")
    state = ParkingLot(expiration_seconds)
    expiration_event = threading.Event()
    cleanup_thread = threading.Thread(target=cleanup_reservations, args=(state, expiration_event), daemon=True)
    cleanup_thread.start()

    num_workers = 8
    worker_queue = queue.Queue()
    for id in range(num_workers):
        thread = threading.Thread(target=work, args=(id, worker_queue, state), daemon=True)
        thread.start()

    accept_conn(app_port, worker_queue)

if __name__ == "__main__":
    main()