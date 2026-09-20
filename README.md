<div align="center">

# gps-redirector

### A pure-Python TCP/UDP redirector for GPS tracking devices. One job: tell every connecting device where to send its data next.

[Quick start](#quick-start) · [Configuration](#configuration) · [Deployment](#deployment) · [Add a manufacturer](#adding-a-new-manufacturer)

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![Dependencies](https://img.shields.io/badge/dependencies-zero-success)](https://github.com/davidneyra2804/gps-redirector)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macOS%20%7C%20windows-lightgrey)](#requirements)
<br/>
<p align="center">
 <a href="https://www.star-history.com/davidneyra2804/gps-redirector"><img src="https://api.star-history.com/badge?repo=davidneyra2804/gps-redirector&type=rank" alt="Star History Rank" /> <img src="https://api.star-history.com/badge?repo=davidneyra2804/gps-redirector&type=trending" alt="GitHub Trending Repository of the Day" /></a>
</p>

</div>

---

## What is gps-redirector?

A lightweight, multi-protocol **GPS redirector** built in pure Python stdlib. It runs in front of any GPS tracking platform (Traccar, OpenGTS, your own SaaS, etc.) and **reconfigures every connecting device** — over TCP or UDP — so the device starts sending its telemetry to a different IP and port of your choice.

It does **not** parse, store, decode, or forward telemetry. It only answers each device's first packet with a GPRS command (Teltonika: `setparam 2004:IP;2005:PUERTO;2006:0`) and lets the device handle the rest. That single behavior is enough to power **fleet-wide server migrations and operational changes** behind a GPS SaaS: move every device from old to new in one shot, no per-device touch, no firmware push, no SIM swap.

```text
GPS device ──► gps-redirector (this project) ──► "go talk to <NEW_IP>:<NEW_PORT> instead"
                                              └─► device re-connects to your real server
```

Built for production on a single VPS: one process per manufacturer/protocol, `python3` only, **no `pip install`, no Docker, no runtime dependencies**. Deploy it as a plain systemd unit.

---

## Why?

Running a GPS tracking business (or a Traccar / OpenGTS instance for a fleet) means you'll eventually have to move devices between servers — a new vendor, a new region, a new SaaS tenant, a rebrand, a failover. Devices in the field only reconfigure themselves when told to over GPRS, and they only listen to GPRS commands from the **server they're currently pointed at**.

`gps-redirector` is the **bridge during that cutover**: stand it up at the old endpoint, point its `TELTONIKA_CMD_TEXT` at the new one, and every reconnecting device is migrated in the background as it pings home. Once traffic drops to zero on the redirector, retire it.

It also doubles as an operational switch: change the destination of an entire fleet by editing one line of `.env` and restarting the service.

---

## Features

- **Pure Python stdlib** — zero external dependencies, zero `pip install`. Runs on any box with `python3`.
- **One script per manufacturer/protocol** — clean separation, runnable in isolation, deployable per-vendor.
- **TCP + UDP in parallel** on the same port (TCP/UDP are distinct L4 protocols — the kernel multiplexes them).
- **IMEI-only parsing** — extracts the IMEI from the device's login packet (TCP codec 8/8-ext handshake, UDP header), nothing else. Telemetry bodies are deliberately ignored.
- **GPRS reconfiguration as the only job** — every valid packet gets a `setparam` once, then `cpureset` to force the device to apply it.
- **Process-based concurrency** — each TCP client and each listener (TCP/UDP) runs in its own process, non-daemon.
- **Per-script logging** — every script writes its own `.log` (RX/TX with UTC-5 timestamps) and a `<script>_imei.log` (one line per unique IMEI, 24h TTL) for fleet accounting.
- **`.env`-based config** — env vars override `.env`, which override code defaults. No hardcoded targets in the repo.
- **Per-connection idle watchdog** — after `TELTONIKA_SOCKET_TIMEOUT` seconds of silence, the per-client TCP connection is closed and released; the listening socket keeps accepting. UDP's `recvfrom` timeout just logs `UDP idle timeout (still listening)` and loops.
- **Disk-failure resilient** — logging is wrapped in `try/except OSError`; an I/O failure logs to stdout and never kills the process.

---

## Supported protocols

| Manufacturer | Script | Transport | What we parse | What we send | Status |
|---|---|---|---|---|---|
| **Teltonika** | `teltonika.py` | TCP + UDP on port `37540` | TCP: IMEI from codec 8 / 8-extended login (17B, IMEI at bytes `[2:17]`). UDP: IMEI from header `packet_type == 0x01`. | `setparam 2004:IP;2005:PUERTO;2006:0` (first packet) → `cpureset` (subsequent) | ✅ Stable |
| **Ruptela** | _planned_ | TCP | IMEI handshake | Ruptela GPRS command (TBD) | 🚧 |
| _Other GPRS-reconfigurable vendors_ | — | — | — | — | [Contributions welcome](#adding-a-new-manufacturer) |

The key invariant across vendors: **parse just enough to learn the IMEI, then push the new IP/port**. Telemetry stays on the device.

See [`AGENTS.md`](./AGENTS.md) for the in-depth per-manufacturer implementation notes and codec details.

---

## Requirements

- **Python 3.10+** (uses modern type hints: `tuple[...]`, PEP 604 unions, structural pattern matching).
- A reachable host with TCP+UDP port `37540` open (default).
- **Nothing else.** No packages, no compilers, no internet at runtime.

---

## Quick start

```bash
git clone https://github.com/davidneyra2804/gps-redirector.git
cd gps-redirector
cp .env.example .env
# edit .env — point TELTONIKA_CMD_TEXT at your target server (IP, port, APN)
python3 teltonika.py
```

That's it. The server is now listening on `0.0.0.0:37540` (TCP + UDP) and will redirect every connecting Teltonika device to the destination configured in `.env`.

Smoke-test locally with `nc`:

```bash
nc localhost 37540        # TCP
# or send a fake UDP datagram with the 8-byte header + IMEI
```

---

## Configuration

A single `.env` file at the repo root (NOT versioned — see `.gitignore`) is parsed by `_config.load_env(prefix, defaults)` in each script.

**Resolution order (highest to lowest priority):**

1. OS environment variable.
2. Entry in `.env` whose name starts with `prefix`.
3. Default value passed to `load_env`.

**Recognized variables (Teltonika as example):**

| Variable | Description | Default |
|---|---|---|
| `TELTONIKA_TCP_PORT` | TCP port the script binds to | `37540` |
| `TELTONIKA_UDP_PORT` | UDP port the script binds to (same number as TCP by default; distinct L4 protocol) | `37540` |
| `TELTONIKA_CMD_TEXT` | GPRS redirect command sent to the device | `setparam 2004:51.161.45.73;2005:2900;2006:0` |
| `TELTONIKA_SOCKET_TIMEOUT` | `recv` / `recvfrom` timeout in seconds (acts as inactivity watchdog) | `300` |

Each new manufacturer follows the same naming pattern: `<FABRICANTE>_*`.

### What we parse — and what we deliberately don't

The redirector's job is to identify the device and tell it where to go next. Everything else is noise.

**TCP (Teltonika codec 8 / 8-extended):**
- Only the **login frame** is inspected — the first 17 bytes (`\x00\x00` + 15 ASCII digits), where `[2:17]` carries the IMEI.
- We **don't decode** the codec 8 / 8-extended AVL body, GPS records, IO elements, or CRC. The body bytes are ignored.
- First packet from an IMEI → `setparam` (your target server). Subsequent packets from the same IMEI → `cpureset`, forcing the device to apply the queued config even if it ignored the first response.

**UDP (Teltonika):**
- Only the **header** is parsed (`length 2B + packetId 2B + type 1B + avlId 1B + imeiLen 2B + imei`). We pull `imei` and stop.
- The rest of the datagram (AVL data) is **not decoded** — it's logged as hex for debugging but never interpreted.
- Same first-packet-per-IMEI semantics as TCP. Header invalid → no reply, no state mutation.

In both transports the keying is **by IMEI, not by source address** — this resists NAT rebinds on UDP and reconnect storms on TCP.

---

## Usage

Three ways to run it, pick whichever fits the box.

### 1. Foreground (development / quick check)

```bash
python3 teltonika.py
```

Stop with `Ctrl+C`. Useful for poking at it over `nc localhost 37540`.

### 2. Background with `nohup`

For quick production-like runs without writing a unit file:

```bash
nohup python3 teltonika.py > /dev/null 2>&1 &
echo $! > gps-redirector.pid
```

Stop it with `kill $(cat gps-redirector.pid)`. Logs still land in `logs/teltonika.log` regardless of the redirection — the script writes them itself; `nohup` only governs stdout/stderr.

### 3. Systemd service (recommended for VPS)

The project ships no Docker, no virtualenv — just `python3 teltonika.py`. The `.env` is loaded via `EnvironmentFile`.

Drop this unit file at `/etc/systemd/system/gps-redirector.service`:

```ini
[Unit]
Description=GPS Redirector (Teltonika TCP+UDP :37540)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=gps
WorkingDirectory=/opt/gps-redirector
EnvironmentFile=/opt/gps-redirector/.env
ExecStart=/usr/bin/python3 /opt/gps-redirector/teltonika.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Activate it:

```bash
systemctl daemon-reload
systemctl enable --now gps-redirector.service
journalctl -u gps-redirector.service -f
```

To **change the redirect target** (e.g. cut over to a new Traccar / OpenGTS host), edit `TELTONIKA_CMD_TEXT` in `.env` and `systemctl restart gps-redirector.service`. Every device that reconnects will get the new destination on its first packet.

### Operational notes

- **Logs**: written to `logs/teltonika.log` (per-connection RX/TX) and `logs/teltonika_imei.log` (one line per unique IMEI, 24h TTL). Watch `teltonika_imei.log` to confirm the fleet is actually migrating through the redirector.
- **Watchdog**: after `TELTONIKA_SOCKET_TIMEOUT` seconds of silence on a TCP connection, the per-client connection is closed (`TCP idle timeout, closing connection: <addr>`) and the client process exits. The listening socket on `TELTONIKA_TCP_PORT` keeps accepting new connections — **the server is never killed by inactivity**. On UDP, the `recvfrom` times out, prints `UDP idle timeout (still listening)` once, and loops.
- **Memory bounded**: UDP keeps dicts keyed by IMEI purged every 24h; TCP state is released on connection close.
- **Disk-failure safe**: `log_message` and `log_imei_once` are wrapped in `try/except OSError`; an I/O failure logs to stdout and never kills the process.

---

## Use case: a Teltonika fleet migration, end to end

You run a GPS SaaS on `old.example.com:2900` and want every device in the field to move to `new.example.com:2900` — without touching a single unit.

1. Spin up `gps-redirector` on a cheap VPS that **also owns the IP the devices currently dial**. In most cutovers that's the same box where `old.example.com` resolves today.
2. Set `TELTONIKA_CMD_TEXT=setparam 2004:new.example.com;2005:2900;2006:0` in `.env`.
3. Start the service. Point the DNS / firewall so Teltonika devices still hit this host on TCP+UDP `37540`.
4. As each device wakes up on its normal cadence and connects, the redirector answers with `setparam` → device reboots → reconnects to `new.example.com:2900`.
5. Watch `logs/teltonika_imei.log` to count how many unique devices have been redirected. When new IMEIs stop appearing for a full reporting cycle, the migration is done — retire the redirector and let DNS expire.

The same flow applies to **failover** (point at the backup server when the primary is down), **multi-tenant reshuffles** (move a subset of devices to a dedicated Traccar instance), and **SaaS plan changes** (move a customer from a shared pool to a dedicated pool by IMEI range, using a separate redirector instance with its own `.env`).

---

## Adding a new manufacturer

The protocol surface is intentionally tiny: parse IMEI, build a GPRS command, send it. To add a vendor:

1. Copy the structure of `teltonika.py` into `<manufacturer>.py` (e.g. `ruptela.py`).
2. Implement the **command constructor** for that vendor's codec (Teltonika uses codec 12 + CRC-16/IBM; Ruptela uses its own framing — see vendor docs and `docs/`).
3. Implement the **IMEI extraction** for the vendor's login frame (Ruptela: ASCII IMEI right after a 2-byte length prefix; confirm against `docs/`).
4. Set `TCP_PORT`, `UDP_PORT`, `COMMAND_TEXT` and the "handshake vs data" detection in `handle_client` / `handle_udp_server`.
5. Add `<FABRICANTE>_TCP_PORT`, `<FABRICANTE>_UDP_PORT`, `<FABRICANTE>_CMD_TEXT`, `<FABRICANTE>_SOCKET_TIMEOUT` entries to `.env.example`.
6. Smoke-test with `python3 <manufacturer>.py` and `nc localhost <port>` or a real device.

Full protocol notes per manufacturer live in [`AGENTS.md`](./AGENTS.md) and `docs/` (the latter is not versioned).

---

## Contributing

PRs welcome, especially for new manufacturer codecs. Keep the rule in mind: **this project parses IMEIs and sends GPRS commands — nothing else**. Don't add telemetry decoders, don't add forwarding logic, don't add database sinks. The whole point is the redirect.

---

## Author

**David Neyra** — [@davidneyra2804](https://github.com/davidneyra2804)

Maintained as an open-source utility for GPS fleet operators who need a dead-simple, dependency-free redirector in front of their tracking servers.

## License

Released under the **MIT License**. See [`LICENSE`](./LICENSE) for the full text.

You are free to use, modify, distribute, and sell derivatives of this software, provided you keep the copyright notice and license intact. No warranty.

---

<p align="center">
 <a href="https://www.star-history.com/davidneyra2804/gps-redirector"><img src="https://api.star-history.com/badge?repo=davidneyra2804/gps-redirector&type=rank" alt="Star History Rank" /> <img src="https://api.star-history.com/badge?repo=davidneyra2804/gps-redirector&type=trending" alt="GitHub Trending Repository of the Day" /></a>
</p>