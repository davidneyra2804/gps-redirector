#!/usr/bin/env python3
"""TCP + UDP server for Teltonika GPS devices on port 37540."""

import os
import signal
import socket
import multiprocessing
import time
from datetime import datetime, timezone, timedelta

from _config import load_env


UTC_MINUS_5 = timezone(timedelta(hours=-5))
PROTOCOL = "teltonika"
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, os.path.splitext(os.path.basename(__file__))[0] + ".log")
IMEI_LOG_FILE = os.path.join(LOG_DIR, os.path.splitext(os.path.basename(__file__))[0] + "_imei.log")

_CFG = load_env(
    "TELTONIKA_",
    {
        "TELTONIKA_TCP_PORT": "37540",
        "TELTONIKA_UDP_PORT": "37540",
        "TELTONIKA_CMD_TEXT": "setparam 2004:51.161.45.73;2005:2900;2006:0",
        "TELTONIKA_SOCKET_TIMEOUT": "300",
    },
)
TCP_PORT = int(_CFG["TELTONIKA_TCP_PORT"])
UDP_PORT = int(_CFG["TELTONIKA_UDP_PORT"])
COMMAND_TEXT = _CFG["TELTONIKA_CMD_TEXT"]
SOCKET_TIMEOUT = int(_CFG["TELTONIKA_SOCKET_TIMEOUT"])


def _reflect(value: int, width: int) -> int:
    """Reverse the bits of value."""
    result = 0
    for _ in range(width):
        result = (result << 1) | (value & 1)
        value >>= 1
    return result


def crc16_ibm(data: bytes) -> int:
    """CRC-16/IBM with input/output reflection."""
    crc = 0x0000
    for byte in data:
        crc ^= _reflect(byte, 8) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x8005
            else:
                crc <<= 1
            crc &= 0xFFFF
    return _reflect(crc, 16)


def make_teltonika_cmd(cmd_str: str) -> bytes:
    """Build a Teltonika codec-12 command packet."""
    content = (cmd_str).encode("ascii")

    packet = (
        (0).to_bytes(4, "big") +
        (len(content) + 8).to_bytes(4, "big") +
        (12).to_bytes(1, "big") +
        (1).to_bytes(1, "big") +
        (5).to_bytes(1, "big") +
        len(content).to_bytes(4, "big") +
        content +
        (1).to_bytes(1, "big")
    )

    crc = crc16_ibm(packet[8:])
    packet += crc.to_bytes(4, "big")

    return packet


def make_teltonika_udp_ack(avl_packet_id: int, count: int) -> bytes:
    """Build a Teltonika UDP ACK packet (5 bytes)."""
    return (
        (5).to_bytes(2, "big") +
        (0).to_bytes(2, "big") +
        (1).to_bytes(1, "big") +
        avl_packet_id.to_bytes(1, "big") +
        count.to_bytes(1, "big")
    )


def parse_udp_header(data: bytes) -> dict | None:
    """Parse a Teltonika UDP datagram header.

    Returns dict with avl_packet_id, imei and payload, or None if malformed.
    """
    if len(data) < 8:
        return None
    packet_type = data[4]
    avl_packet_id = data[5]
    if packet_type != 0x01:
        return None
    if len(data) < 10:
        return None
    imei_len = int.from_bytes(data[6:8], "big")
    if len(data) < 8 + imei_len:
        return None
    imei = data[8:8 + imei_len].decode("ascii", errors="replace")
    payload = data[8 + imei_len:]
    return {
        "avl_packet_id": avl_packet_id,
        "imei": imei,
        "payload": payload,
    }


def log_message(addr, rx_hex: str, rx_decoded: str, tx_hex: str):
    """Append a log entry to the log file."""
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now(UTC_MINUS_5).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} | {addr} | RX: {rx_hex} | DECODED: {rx_decoded} | TX: {tx_hex}\n"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except OSError as e:
        print(f"[{PROTOCOL}][PID {multiprocessing.current_process().pid}] log write failed: {e}")


def _purge_expired(store: dict, ttl: float):
    """Remove entries older than ttl seconds from a {key: timestamp} dict."""
    cutoff = time.monotonic() - ttl
    expired = [k for k, ts in store.items() if ts < cutoff]
    for k in expired:
        del store[k]


def log_imei_once(seen: dict, proto: str, imei: str, ttl: float = 86400.0):
    """Append a unique IMEI entry to the IMEI ledger.

    `seen` is a {imei: timestamp} dict mutated in-place. Entries older than
    `ttl` seconds are purged on each call. Default TTL = 24h.
    """
    _purge_expired(seen, ttl)
    if imei in seen:
        return
    seen[imei] = time.monotonic()
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now(UTC_MINUS_5).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} | {proto} | IMEI: {imei}\n"
    try:
        with open(IMEI_LOG_FILE, "a") as f:
            f.write(line)
    except OSError as e:
        print(f"[{PROTOCOL}][PID {multiprocessing.current_process().pid}] IMEI log write failed: {e}")


def _wake_by_close(sock: socket.socket):
    def _handler(*_):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    return _handler


def handle_client(conn: socket.socket, addr: tuple, shutdown_event=None):
    """Handle a single TCP client connection in a separate process.

    The connection is closed after SOCKET_TIMEOUT seconds of inactivity
    (recv returns TimeoutError). The listening socket in tcp_server is
    unaffected — only this per-client connection is released. A new
    connection from the same IMEI will be treated as fresh and receive
    the setparam response again.
    """
    pid = multiprocessing.current_process().pid
    print(f"[{PROTOCOL}][PID {pid}] TCP Connected: {addr}")
    conn.settimeout(SOCKET_TIMEOUT)
    signal.signal(signal.SIGTERM, _wake_by_close(conn))
    signal.signal(signal.SIGINT, _wake_by_close(conn))
    seen_imei = {}
    redirected_imei = {}
    try:
        while True:
            if shutdown_event is not None and shutdown_event.is_set():
                break
            try:
                data = conn.recv(4096)
            except TimeoutError:
                print(f"[{PROTOCOL}][PID {pid}] TCP idle timeout, closing connection: {addr}")
                break
            except OSError:
                break
            if not data:
                break

            hex_data = data.hex()
            try:
                decoded = data.decode("utf-8", errors="replace").strip()
            except Exception:
                decoded = "(decode error)"

            print(f"[{PROTOCOL}][PID {pid}] TCP RX ({len(data)} bytes): {hex_data}")

            imei = None
            if len(data) >= 17:
                candidate = data[2:17].decode("ascii", errors="replace")
                if candidate.isdigit() and len(candidate) == 15:
                    imei = candidate
                    log_imei_once(seen_imei, "TCP", imei)

            if imei and imei not in redirected_imei:
                cmd_text = COMMAND_TEXT
                response = make_teltonika_cmd(cmd_text)
                redirected_imei[imei] = time.monotonic()
            else:
                cmd_text = "cpureset"
                response = make_teltonika_cmd(cmd_text)

            conn.sendall(response)
            print(f"[{PROTOCOL}][PID {pid}] TCP TX ({len(response)} bytes): {response.hex()} | CMD: {cmd_text}")
            log_message(addr, hex_data, decoded, response.hex())

    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        print(f"[{PROTOCOL}][PID {pid}] TCP Connection error: {e}")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass
        print(f"[{PROTOCOL}][PID {pid}] TCP Connection closed: {addr}")


def tcp_server(host: str, port: int, shutdown_event=None):
    """TCP accept loop in its own process (non-daemon)."""
    pid = multiprocessing.current_process().pid
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)
    server.settimeout(1.0)
    signal.signal(signal.SIGTERM, _wake_by_close(server))
    signal.signal(signal.SIGINT, _wake_by_close(server))
    print(f"[{PROTOCOL}][PID {pid}] TCP listening on {host}:{port}")
    try:
        while True:
            if shutdown_event is not None and shutdown_event.is_set():
                break
            try:
                conn, addr = server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            client_proc = multiprocessing.Process(
                target=handle_client,
                args=(conn, addr, shutdown_event),
            )
            client_proc.start()
            conn.close()
    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        print(f"[{PROTOCOL}][PID {pid}] TCP accept error: {e}")
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        print(f"[{PROTOCOL}][PID {pid}] TCP server closed")


def handle_udp_server(host: str, port: int, shutdown_event=None):
    """Handle UDP datagrams in a separate process."""
    pid = multiprocessing.current_process().pid
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.settimeout(1.0)
    signal.signal(signal.SIGTERM, _wake_by_close(sock))
    signal.signal(signal.SIGINT, _wake_by_close(sock))
    print(f"[{PROTOCOL}][PID {pid}] UDP listening on {host}:{port}")
    try:
        redirected = {}
        seen_imei = {}
        idle_logged = False
        while True:
            if shutdown_event is not None and shutdown_event.is_set():
                break
            try:
                data, addr = sock.recvfrom(4096)
            except TimeoutError:
                if not idle_logged:
                    print(f"[{PROTOCOL}][PID {pid}] UDP idle timeout (still listening)")
                    idle_logged = True
                continue
            except OSError:
                break
            if not data:
                continue
            idle_logged = False

            hex_data = data.hex()
            parsed = parse_udp_header(data)
            if parsed is None:
                decoded = "(invalid UDP header)"
                response = b""
                cmd_text = ""
            else:
                decoded = f"IMEI={parsed['imei']} AVL_ID={parsed['avl_packet_id']} payload={parsed['payload'].hex()}"
                log_imei_once(seen_imei, "UDP", parsed["imei"])
                _purge_expired(redirected, 86400.0)
                if parsed["imei"] in redirected:
                    cmd_text = "cpureset"
                    response = make_teltonika_cmd(cmd_text)
                else:
                    cmd_text = COMMAND_TEXT
                    response = make_teltonika_cmd(cmd_text)
                    redirected[parsed["imei"]] = time.monotonic()

            print(f"[{PROTOCOL}][PID {pid}] UDP RX ({len(data)} bytes) from {addr}: {hex_data}")
            if response:
                sock.sendto(response, addr)
                print(f"[{PROTOCOL}][PID {pid}] UDP TX ({len(response)} bytes) to {addr}: {response.hex()} | CMD: {cmd_text}")
            log_message(addr, hex_data, decoded, response.hex() if response else "")

    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        print(f"[{PROTOCOL}][PID {pid}] UDP error: {e}")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass
        print(f"[{PROTOCOL}][PID {pid}] UDP socket closed")


def main():
    host = "0.0.0.0"
    shutdown_event = multiprocessing.Event()
    procs = []

    def _shutdown_children(*_):
        shutdown_event.set()
        for p in procs:
            if p.is_alive():
                try:
                    os.kill(p.pid, signal.SIGINT)
                except OSError:
                    pass

    signal.signal(signal.SIGINT, _shutdown_children)
    signal.signal(signal.SIGTERM, _shutdown_children)

    tcp_proc = multiprocessing.Process(
        target=tcp_server,
        args=(host, TCP_PORT, shutdown_event),
    )
    udp_proc = multiprocessing.Process(
        target=handle_udp_server,
        args=(host, UDP_PORT, shutdown_event),
    )
    procs.extend([tcp_proc, udp_proc])
    tcp_proc.start()
    udp_proc.start()

    print(f"[{PROTOCOL}] Server listening on {host}: TCP={TCP_PORT}, UDP={UDP_PORT}")

    try:
        while tcp_proc.is_alive() or udp_proc.is_alive():
            tcp_proc.join(timeout=0.5)
            udp_proc.join(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_event.set()
        for p in procs:
            if p.is_alive():
                try:
                    os.kill(p.pid, signal.SIGINT)
                except OSError:
                    pass
        for p in procs:
            p.join(timeout=3)
        for p in procs:
            if p.is_alive():
                p.terminate()
                p.join(timeout=2)
                if p.is_alive():
                    p.kill()
                    p.join()
        print(f"[{PROTOCOL}] Server stopped.")


if __name__ == "__main__":
    main()
