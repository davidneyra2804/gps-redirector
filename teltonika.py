#!/usr/bin/env python3
"""TCP server for Teltonika GPS devices on port 37540."""

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
        "TELTONIKA_CMD_TEXT": "setparam 2004:51.161.45.73;2005:2900;2006:0",
        "TELTONIKA_SOCKET_TIMEOUT": "300",
    },
)
LISTEN_PORT = int(_CFG["TELTONIKA_TCP_PORT"])
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


def log_message(addr, rx_hex: str, rx_decoded: str, tx_hex: str):
    """Append a log entry to the log file."""
    ts = datetime.now(UTC_MINUS_5).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} | {addr} | RX: {rx_hex} | DECODED: {rx_decoded} | TX: {tx_hex}\n"
    with open(LOG_FILE, "a") as f:
        f.write(line)


def handle_client(conn: socket.socket, addr: tuple):
    """Handle a single client connection in a separate process."""
    print(f"[PID {multiprocessing.current_process().pid}] Connected: {addr}")
    conn.settimeout(SOCKET_TIMEOUT)
    try:
        while True:
            try:
                data = conn.recv(4096)
            except TimeoutError:
                print(f"[PID {multiprocessing.current_process().pid}] Timeout waiting for data: {addr}")
                break
            if not data:
                break

            hex_data = data.hex()
            try:
                decoded = data.decode("utf-8", errors="replace").strip()
            except Exception:
                decoded = "(decode error)"

            print(f"[PID {multiprocessing.current_process().pid}] Received ({len(data)} bytes): {hex_data}")

            wait = False
            if len(data) == 17:
                response = make_teltonika_cmd(COMMAND_TEXT)
            else:
                wait = True
                response = make_teltonika_cmd("cpureset")

            if wait:
                time.sleep(2)
            conn.sendall(response)
            print(f"[PID {multiprocessing.current_process().pid}] Sending ({len(response)} bytes): {response.hex()}")
            log_message(addr, hex_data, decoded, response.hex())

    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        print(f"[PID {multiprocessing.current_process().pid}] Connection error: {e}")
    finally:
        conn.close()
        print(f"[PID {multiprocessing.current_process().pid}] Connection closed: {addr}")


def main():
    host = "0.0.0.0"
    port = LISTEN_PORT

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)

    print(f"Server listening on {host}:{port}")

    try:
        while True:
            conn, addr = server.accept()
            proc = multiprocessing.Process(
                target=handle_client,
                args=(conn, addr),
                daemon=True
            )
            proc.start()
            conn.close()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        server.close()


if __name__ == "__main__":
    main()
