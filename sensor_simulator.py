import random
import socket
import sys
import time


def main():
    # Part C helper: generates UPDATE traffic to the async sensor port
    if len(sys.argv) < 5 or len(sys.argv) > 7:
        print("Usage: python sensor_simulator.py <host> <sensor_port> <updates_per_second> <duration_seconds> [lot_ids_csv] [seed]")
        return

    host = sys.argv[1]
    sensor_port = int(sys.argv[2])
    updates_per_second = float(sys.argv[3])
    duration_seconds = int(sys.argv[4])
    lot_ids = sys.argv[5].split(",") if len(sys.argv) >= 6 else ["G1", "G2", "G3", "G4"]
    if len(sys.argv) == 7:
        random.seed(int(sys.argv[6]))

    if updates_per_second <= 0:
        print("updates_per_second must be > 0")
        return

    sleep_interval = 1.0 / updates_per_second
    end_time = time.time() + duration_seconds

    sock = socket.create_connection((host, sensor_port))
    stream = sock.makefile("rwb")

    queued = 0
    busy = 0
    errors = 0

    try:
        while time.time() < end_time:
            lot_id = random.choice(lot_ids)
            delta = random.choice([1, -1])
            stream.write(f"UPDATE {lot_id} {delta}\\n".encode("utf-8"))
            stream.flush()
            reply = stream.readline().decode("utf-8").strip()
            if reply == "QUEUED":
                queued += 1
            elif reply == "BUSY":
                busy += 1
            else:
                errors += 1
            time.sleep(sleep_interval)
    except KeyboardInterrupt:
        pass
    finally:
        stream.close()
        sock.close()

    print(f"queued={queued} busy={busy} errors={errors}")


if __name__ == "__main__":
    main()
