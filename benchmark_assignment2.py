import argparse
import random
import statistics
import threading
import time
import socket
from dataclasses import dataclass
from typing import List, Tuple

from rpc_client import RPCClient, rpcTimeoutError


@dataclass
class WorkerStats:
    count: int = 0
    errors: int = 0
    timeouts: int = 0
    latencies_ms: List[float] = None

    def __post_init__(self):
        if self.latencies_ms is None:
            self.latencies_ms = []


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int((len(ordered) - 1) * p)
    return ordered[index]


def run_avail_worker(host: str, port: int, lot_id: str, timeout: float, stop_at: float, stats: WorkerStats):
    client = None
    try:
        client = RPCClient(host, port, timeout)
        while time.time() < stop_at:
            start = time.perf_counter()
            try:
                result, error = client.getAvailability(lot_id)
                latency_ms = (time.perf_counter() - start) * 1000.0
                if error is None and result is not None:
                    stats.count += 1
                    stats.latencies_ms.append(latency_ms)
                else:
                    stats.errors += 1
            except rpcTimeoutError:
                stats.timeouts += 1
            except ConnectionError:
                stats.errors += 1
                break
    finally:
        if client:
            client.close()


def run_reserve_worker(
    host: str,
    port: int,
    lot_id: str,
    timeout: float,
    stop_at: float,
    worker_id: int,
    cancel_after_reserve: bool,
    stats: WorkerStats,
):
    client = None
    sequence = 0
    try:
        client = RPCClient(host, port, timeout)
        while time.time() < stop_at:
            plate = f"W{worker_id:02d}P{sequence:08d}"
            sequence += 1
            start = time.perf_counter()
            try:
                reserved, error = client.reserve(lot_id, plate)
                latency_ms = (time.perf_counter() - start) * 1000.0
                stats.count += 1
                stats.latencies_ms.append(latency_ms)
                if error not in (None, "FULL", "EXISTS"):
                    stats.errors += 1
                if reserved and cancel_after_reserve:
                    try:
                        client.cancel(lot_id, plate)
                    except (rpcTimeoutError, ConnectionError):
                        stats.errors += 1
            except rpcTimeoutError:
                stats.timeouts += 1
            except ConnectionError:
                stats.errors += 1
                break
    finally:
        if client:
            client.close()


def sensor_loop(host: str, sensor_port: int, lot_id: str, updates_per_second: float, stop_event: threading.Event, counters: dict):
    sock = None
    stream = None
    try:
        sock = socket.create_connection((host, sensor_port), timeout=3)
        stream = sock.makefile("rwb")
        sleep_interval = 1.0 / updates_per_second
        while not stop_event.is_set():
            delta = random.choice([1, -1])
            stream.write(f"UPDATE {lot_id} {delta}\n".encode("utf-8"))
            stream.flush()
            reply = stream.readline().decode("utf-8").strip()
            if reply == "QUEUED":
                counters["queued"] += 1
            elif reply == "BUSY":
                counters["busy"] += 1
            else:
                counters["errors"] += 1
            time.sleep(sleep_interval)
    except OSError:
        counters["errors"] += 1
    finally:
        if stream:
            stream.close()
        if sock:
            sock.close()


def aggregate_stats(stats_list: List[WorkerStats], duration_seconds: int) -> dict:
    total_count = sum(s.count for s in stats_list)
    total_errors = sum(s.errors for s in stats_list)
    total_timeouts = sum(s.timeouts for s in stats_list)
    latencies = []
    for s in stats_list:
        latencies.extend(s.latencies_ms)

    throughput = total_count / duration_seconds if duration_seconds > 0 else 0.0
    median = statistics.median(latencies) if latencies else 0.0
    p95 = percentile(latencies, 0.95)

    return {
        "requests": total_count,
        "errors": total_errors,
        "timeouts": total_timeouts,
        "throughput": throughput,
        "median_ms": median,
        "p95_ms": p95,
    }


def run_single_benchmark(
    operation: str,
    workers: int,
    duration_seconds: int,
    host: str,
    rpc_port: int,
    lot_id: str,
    timeout: float,
    cancel_after_reserve: bool,
) -> dict:
    stop_at = time.time() + duration_seconds
    threads = []
    stats_list = []

    for worker_id in range(workers):
        stats = WorkerStats()
        stats_list.append(stats)
        if operation == "avail":
            t = threading.Thread(
                target=run_avail_worker,
                args=(host, rpc_port, lot_id, timeout, stop_at, stats),
                daemon=True,
            )
        else:
            t = threading.Thread(
                target=run_reserve_worker,
                args=(host, rpc_port, lot_id, timeout, stop_at, worker_id, cancel_after_reserve, stats),
                daemon=True,
            )
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    return aggregate_stats(stats_list, duration_seconds)


def run_matrix(
    operation: str,
    worker_counts: List[int],
    duration_seconds: int,
    host: str,
    rpc_port: int,
    lot_id: str,
    timeout: float,
    cancel_after_reserve: bool,
    sensor_port: int,
    sensor_rate: float,
    sensor_lots: List[str],
) -> List[Tuple[int, dict]]:
    results = []

    for workers in worker_counts:
        sensor_stop = threading.Event()
        sensor_threads = []
        sensor_counters = {"queued": 0, "busy": 0, "errors": 0}

        if sensor_rate > 0:
            for lot in sensor_lots:
                thread = threading.Thread(
                    target=sensor_loop,
                    args=(host, sensor_port, lot, sensor_rate, sensor_stop, sensor_counters),
                    daemon=True,
                )
                thread.start()
                sensor_threads.append(thread)

        result = run_single_benchmark(
            operation=operation,
            workers=workers,
            duration_seconds=duration_seconds,
            host=host,
            rpc_port=rpc_port,
            lot_id=lot_id,
            timeout=timeout,
            cancel_after_reserve=cancel_after_reserve,
        )

        if sensor_rate > 0:
            sensor_stop.set()
            for thread in sensor_threads:
                thread.join(timeout=2)
            result["sensor_queued"] = sensor_counters["queued"]
            result["sensor_busy"] = sensor_counters["busy"]
            result["sensor_errors"] = sensor_counters["errors"]

        results.append((workers, result))

    return results


def print_results(title: str, operation: str, rows: List[Tuple[int, dict]], include_sensor: bool):
    print("\n" + title)
    print("operation,workers,requests,errors,timeouts,throughput_req_per_s,median_ms,p95_ms" + (",sensor_queued,sensor_busy,sensor_errors" if include_sensor else ""))
    for workers, result in rows:
        base = (
            f"{operation},{workers},{result['requests']},{result['errors']},{result['timeouts']},"
            f"{result['throughput']:.2f},{result['median_ms']:.2f},{result['p95_ms']:.2f}"
        )
        if include_sensor:
            base += f",{result.get('sensor_queued', 0)},{result.get('sensor_busy', 0)},{result.get('sensor_errors', 0)}"
        print(base)


def parse_args():
    parser = argparse.ArgumentParser(description="Assignment 2 benchmark harness")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--rpc-port", type=int, default=1234)
    parser.add_argument("--sensor-port", type=int, default=1235)
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--workers", default="1,4,8,16")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--lot", default="G1")
    parser.add_argument("--sensor-lots", default="G1,G2,G3,G4")
    parser.add_argument("--sensor-rate", type=float, default=0.0, help="updates/sec per lot (0 = off)")
    parser.add_argument("--op", choices=["avail", "reserve", "both"], default="both")
    parser.add_argument("--no-cancel-after-reserve", action="store_true", help="Do not cancel after successful reserve")
    return parser.parse_args()


def main():
    args = parse_args()
    worker_counts = [int(item.strip()) for item in args.workers.split(",") if item.strip()]
    sensor_lots = [item.strip() for item in args.sensor_lots.split(",") if item.strip()]
    cancel_after_reserve = not args.no_cancel_after_reserve
    include_sensor = args.sensor_rate > 0

    if args.op in ("avail", "both"):
        rows = run_matrix(
            operation="avail",
            worker_counts=worker_counts,
            duration_seconds=args.duration,
            host=args.host,
            rpc_port=args.rpc_port,
            lot_id=args.lot,
            timeout=args.timeout,
            cancel_after_reserve=cancel_after_reserve,
            sensor_port=args.sensor_port,
            sensor_rate=args.sensor_rate,
            sensor_lots=sensor_lots,
        )
        title = "Async+PubSub Stress (AVAIL)" if include_sensor else "Sync Baseline (AVAIL)"
        print_results(title, "avail", rows, include_sensor)

    if args.op in ("reserve", "both"):
        rows = run_matrix(
            operation="reserve",
            worker_counts=worker_counts,
            duration_seconds=args.duration,
            host=args.host,
            rpc_port=args.rpc_port,
            lot_id=args.lot,
            timeout=args.timeout,
            cancel_after_reserve=cancel_after_reserve,
            sensor_port=args.sensor_port,
            sensor_rate=args.sensor_rate,
            sensor_lots=sensor_lots,
        )
        title = "Async+PubSub Stress (RESERVE)" if include_sensor else "Sync Baseline (RESERVE)"
        print_results(title, "reserve", rows, include_sensor)


if __name__ == "__main__":
    main()
