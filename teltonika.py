#!/usr/bin/env python3
"""TCP + UDP server for Teltonika GPS devices on port 37540."""

import os
import socket
import multiprocessing
import time
from datetime import datetime, timezone, timedelta

from _config import load_env


UTC_MINUS_5 = timezone(timedelta(hours=-5))
LOG_FILE = os.path.splitext(os.path.basename(__file__))[0] + ".log"

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
    packet_id = int.from_bytes(data[2:4], "big")
    packet_type = data[4]
    avl_packet_id = data[5]
    if packet_type != 0x01 or packet_id != 0:
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
    ts = datetime.now(UTC_MINUS_5).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} | {addr} | RX: {rx_hex} | DECODED: {rx_decoded} | TX: {tx_hex}\n"
    with open(LOG_FILE, "a") as f:
        f.write(line)


def handle_client(conn: socket.socket, addr: tuple):
    """Handle a single TCP client connection in a separate process."""
    pid = multiprocessing.current_process().pid
    print(f"[PID {pid}] TCP Connected: {addr}")
    conn.settimeout(SOCKET_TIMEOUT)
    try:
        while True:
            try:
                data = conn.recv(4096)
            except TimeoutError:
                print(f"[PID {pid}] TCP Timeout waiting for data: {addr}")
                break
            if not data:
                break

            hex_data = data.hex()
            try:
                decoded = data.decode("utf-8", errors="replace").strip()
            except Exception:
                decoded = "(decode error)"

            print(f"[PID {pid}] TCP RX ({len(data)} bytes): {hex_data}")

            wait = False
            if len(data) == 17:
                response = make_teltonika_cmd(COMMAND_TEXT)
            else:
                wait = True
                response = make_teltonika_cmd("cpureset")

            if wait:
                time.sleep(2)
            conn.sendall(response)
            print(f"[PID {pid}] TCP TX ({len(response)} bytes): {response.hex()}")
            log_message(addr, hex_data, decoded, response.hex())

    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        print(f"[PID {pid}] TCP Connection error: {e}")
    finally:
        conn.close()
        print(f"[PID {pid}] TCP Connection closed: {addr}")


def tcp_server(host: str, port: int):
    """TCP accept loop in its own process (non-daemon)."""
    pid = multiprocessing.current_process().pid
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)
    print(f"[PID {pid}] TCP listening on {host}:{port}")
    try:
        while True:
            conn, addr = server.accept()
            client_proc = multiprocessing.Process(
                target=handle_client,
                args=(conn, addr),
            )
            client_proc.start()
            conn.close()
    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        print(f"[PID {pid}] TCP accept error: {e}")
    finally:
        server.close()
        print(f"[PID {pid}] TCP server closed")


def handle_udp_server(host: str, port: int):
    """Handle UDP datagrams in a separate process."""
    pid = multiprocessing.current_process().pid
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.settimeout(SOCKET_TIMEOUT)
    print(f"[PID {pid}] UDP listening on {host}:{port}")
    try:
        while True:
            try:
                data, addr = sock.recvfrom(4096)
            except TimeoutError:
                print(f"[PID {pid}] UDP timeout, exiting loop")
                break
            if not data:
                continue

            hex_data = data.hex()
            parsed = parse_udp_header(data)
            if parsed is None:
                decoded = "(invalid UDP header)"
                ack = b""
            else:
                decoded = f"IMEI={parsed['imei']} AVL_ID={parsed['avl_packet_id']} payload={parsed['payload'].hex()}"
                records_count = parsed["payload"][4] if len(parsed["payload"]) >= 5 else 0
                ack = make_teltonika_udp_ack(parsed["avl_packet_id"], records_count)

            print(f"[PID {pid}] UDP RX ({len(data)} bytes) from {addr}: {hex_data}")
            if ack:
                sock.sendto(ack, addr)
                print(f"[PID {pid}] UDP TX ({len(ack)} bytes) to {addr}: {ack.hex()}")
            log_message(addr, hex_data, decoded, ack.hex() if ack else "")

    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        print(f"[PID {pid}] UDP error: {e}")
    finally:
        sock.close()
        print(f"[PID {pid}] UDP socket closed")


def main():
    host = "0.0.0.0"

    tcp_proc = multiprocessing.Process(
        target=tcp_server,
        args=(host, TCP_PORT),
    )
    udp_proc = multiprocessing.Process(
        target=handle_udp_server,
        args=(host, UDP_PORT),
    )
    tcp_proc.start()
    udp_proc.start()

    print(f"Server listening on {host}: TCP={TCP_PORT}, UDP={UDP_PORT}")

    try:
        tcp_proc.join()
        udp_proc.join()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        for p in (tcp_proc, udp_proc):
            if p.is_alive():
                p.terminate()
                p.join()


if __name__ == "__main__":
    main()
