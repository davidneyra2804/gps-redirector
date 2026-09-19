# gps-redirector

> Pure-Python TCP/UDP redirector for GPS tracking devices. Sits in front of any GPS device fleet and steers incoming connections toward a configurable destination server — no telemetry processing, no third-party dependencies, just `python3`.

A lightweight, multi-protocol **GPS redirector** built in pure Python stdlib. Each manufacturer/protocol runs as its own standalone script: when a compatible GPS device connects (Teltonika codec 12 today, Concox / GT06 / others tomorrow), the server replies with a GPRS command that **redirects the device to a different IP and port** of your choice. It does not store, parse, or forward telemetry — it only instructs the device where to send its data.

Designed for production on a single VPS, deployable as a plain systemd unit. No Docker, no `pip install`, no runtime dependencies.

---

## Table of contents

- [Features](#features)
- [Supported protocols](#supported-protocols)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Usage](#usage)
- [Deployment](#deployment)
- [Adding a new manufacturer](#adding-a-new-manufacturer)
- [Author](#author)
- [License](#license)

---

## Features

- **Pure Python stdlib** — zero external dependencies, zero `pip install`.
- **One script per manufacturer/protocol** — clean separation, runnable in isolation.
- **TCP + UDP in parallel** on the same port (different L4 protocols — the kernel handles both).
- **Process-based concurrency** — each TCP client and each listener (TCP/UDP) runs in its own process.
- **Per-script logging** — every `.py` writes its own `.log` file with timestamps in UTC-5.
- **Unique IMEI accounting** — a second `<script>_imei.log` records one line per distinct IMEI seen (TTL 24h), useful for fleet sizing.
- **`.env`-based config** — environment variables override `.env` values, which override code defaults. No hardcoded secrets in the repo.
- **Watchdog without socket death** — idle timeout logs `idle timeout (still listening)` and keeps the loop alive.
- **Disk-failure resilient** — logging is wrapped in `try/except OSError`; an I/O failure logs to stdout and never kills the process.

## Supported protocols

| Manufacturer | Script | Transport | Codec | Status |
|---|---|---|---|---|
| **Teltonika** | `teltonika.py` | TCP + UDP on port `37540` | Codec 12 (CRC-16/IBM) | ✅ Stable |
| Concox | _planned_ | — | — | 🚧 |
| GT06 | _planned_ | — | — | 🚧 |
| _Your protocol here_ | — | — | — | [Contributions welcome](#adding-a-new-manufacturer) |

See [`AGENTS.md`](./AGENTS.md) for the in-depth protocol notes and per-manufacturer implementation details.

## Requirements

- **Python 3.10+** (uses modern type hints: `tuple[...]`, PEP 604 unions, structural pattern matching).

No third-party packages required.

## Quick start

```bash
git clone https://github.com/davidneyra2804/gps-redirector.git
cd gps-redirector
cp .env.example .env
# edit .env to point TELTONIKA_CMD_TEXT at your target server
python3 teltonika.py
```

That's it. The server is now listening on `0.0.0.0:37540` (TCP + UDP) and will redirect every connecting Teltonika device to the destination configured in `.env`.

## Configuration

Configuration is loaded from a single `.env` file at the repo root (NOT versioned — see `.gitignore`). It is parsed by `_config.load_env(prefix, defaults)` in each script.

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

Each new manufacturer follows the same naming pattern: `<FABRICANT>_*`.

## Usage

Each script runs standalone:

```bash
python3 teltonika.py
```

Local smoke test with `nc`:

```bash
nc localhost 37540
```

See [`AGENTS.md`](./AGENTS.md) for protocol-level details and how to add a new manufacturer.

## Deployment

This project is **pure Python, no external dependencies** — designed to run directly on a VPS via systemd. No Docker required.

### Local (development)

```bash
python3 teltonika.py
```

### Production (systemd on a VPS)

Binary to run: `python3 teltonika.py`. No virtualenv, no `pip install`. Configuration lives in `.env` (NOT versioned) and is loaded by `_config.load_env`.

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

### Operational notes

- **Logs**: written to `logs/teltonika.log` (per-connection RX/TX) and `logs/teltonika_imei.log` (one line per unique IMEI, 24h TTL).
- **Watchdog**: after `TELTONIKA_SOCKET_TIMEOUT` seconds of silence, the server logs `idle timeout (still listening)` and the loop continues — **the server is never killed by inactivity**.
- **Memory bounded**: UDP keeps dicts keyed by IMEI purged every 24h; TCP state is released on connection close.
- **Disk-failure safe**: `log_message` and `log_imei_once` are wrapped in `try/except OSError`; an I/O failure logs to stdout and never kills the process.
- **Change redirect target**: edit `TELTONIKA_CMD_TEXT` in `.env` and run `systemctl restart gps-redirector.service`.

## Adding a new manufacturer

1. Copy the structure of `teltonika.py` into `<manufacturer>.py`.
2. Implement the command constructor for the manufacturer's codec (Teltonika uses codec 12 + CRC-16/IBM; other vendors differ).
3. Set `TCP_PORT`, `UDP_PORT`, `COMMAND_TEXT` and the "handshake vs data" detection logic in `handle_client` / `handle_udp_server`.
4. Add the corresponding `<FABRICANT>_TCP_PORT`, `<FABRICANT>_UDP_PORT`, `<FABRICANT>_CMD_TEXT`, `<FABRICANT>_SOCKET_TIMEOUT` entries to `.env.example`.
5. Smoke-test by running `python3 <manufacturer>.py` and connecting via `nc` or a real device.

Full protocol notes per manufacturer live in [`AGENTS.md`](./AGENTS.md) and `docs/` (the latter is not versioned).

---

## Author

**David Neyra** — [@davidneyra2804](https://github.com/davidneyra2804)

Maintained as an open-source utility for GPS fleet operators who need a dead-simple, dependency-free redirector in front of their tracking servers.

## License

Released under the **MIT License**. See [`LICENSE`](./LICENSE) for the full text.

You are free to use, modify, distribute, and sell derivatives of this software, provided you keep the copyright notice and license intact. No warranty.
