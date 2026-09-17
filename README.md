# python-gps-redirect-gprs

Conjunto de interfaces TCP en Python puro para actuar como **redirector de servidores GPS**. Cada script levanta un puerto y, cuando un dispositivo GPS compatible se conecta, le responde con un comando GPRS que lo redirige a otro servidor (IP/puerto).

## Características

- **Sin dependencias externas**: solo librerías estándar de Python.
- **Un script por fabricante**: cada modelo (Teltonika, Concox, GT06, etc.) se atiende en su propio archivo ejecutable.
- **TCP + UDP en paralelo** sobre el mismo puerto (Teltonika = 37540 en ambos protocolos).
- **Concurrencia por proceso**: cada cliente TCP y cada listener (TCP/UDP) corre en su propio proceso.
- **Logging por script**: cada `.py` genera su propio `.log` con timestamps en UTC-5.
- **Configuración via `.env`** (no versionado) con fallback a constantes y override por variable de entorno.

## Requisitos

- **Python 3.10+** (uso de type hints con `tuple` y sintaxis moderna).

## Estructura

```
python-gps-redirect-gprs/
├── _config.py          # Loader de .env sin dependencias externas
├── .env.example        # Plantilla de variables (sí versionada)
├── .gitignore          # Ignora .env, *.log y docs/
├── teltonika.py        # Servidor TCP + UDP en puerto 37540 (codec 12 / CRC-16/IBM)
├── logs/               # (generado) teltonika.log y teltonika_imei.log
├── docs/               # (no versionado) documentación de codecs por fabricante
└── AGENTS.md           # Guía de contexto y convenciones para el agente
```

## Configuración

Copiar `.env.example` a `.env` y editar:

```bash
cp .env.example .env
```

Variables reconocidas (Teltonika como ejemplo):

| Variable | Descripción | Default |
|----------|-------------|---------|
| `TELTONIKA_TCP_PORT` | Puerto TCP donde escucha | `37540` |
| `TELTONIKA_UDP_PORT` | Puerto UDP donde escucha (mismo que TCP por defecto) | `37540` |
| `TELTONIKA_CMD_TEXT` | Comando GPRS de redirección | `setparam 2004:51.161.45.73;2005:2900;2006:0` |
| `TELTONIKA_SOCKET_TIMEOUT` | Timeout de `recv`/`recvfrom` en segundos | `300` |

Orden de resolución (mayor a menor prioridad):
1. Variable de entorno del sistema.
2. Entrada en `.env`.
3. Default en el código.

## Uso

Cada script se ejecuta de forma standalone:

```bash
python3 teltonika.py
```

Para probar localmente:

```bash
nc localhost 37540
```

Ver `AGENTS.md` para detalles del protocolo de cada fabricante y cómo agregar nuevos modelos.

## Despliegue

El proyecto es **Python puro sin dependencias externas**, pensado para correr directamente en el VPS de producción con `python3 teltonika.py`. No usa Docker.

### Local (desarrollo)

```bash
python3 teltonika.py
```

### Producción (systemd en VPS)

El binario a ejecutar es `python3 teltonika.py`. Sin virtualenv, sin `pip install`. La configuración vive en `.env` (NO versionado) y se carga vía `_config.load_env`.

Unit file en `/etc/systemd/system/python-gps-redirect-gprs.service`:

```ini
[Unit]
Description=Teltonika GPS redirector (TCP+UDP :37540)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=gps
WorkingDirectory=/opt/python-gps-redirect-gprs
EnvironmentFile=/opt/python-gps-redirect-gprs/.env
ExecStart=/usr/bin/python3 /opt/python-gps-redirect-gprs/teltonika.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Activar:

```bash
systemctl daemon-reload
systemctl enable --now python-gps-redirect-gprs.service
journalctl -u python-gps-redirect-gprs.service -f
```

### Notas operativas

- **Logs**: se escriben en `logs/teltonika.log` (RX/TX por conexión) y `logs/teltonika_imei.log` (1 línea por IMEI único, TTL 24h).
- **Watchdog**: si pasan `TELTONIKA_SOCKET_TIMEOUT` segundos sin tráfico, se loguea `idle timeout (still listening)` y el loop continúa. El server **no se cierra por inactividad**.
- **Memoria**: el proceso UDP lleva dicts `{imei: timestamp}` purgados cada 24h, evitando leak en sesiones largas.
- **Fallo de disco**: `log_message` y `log_imei_once` están blindados con `try/except OSError`; un fallo de E/S no mata al proceso, solo se loguea por stdout.
- **Cambiar destino de redirección**: editar `TELTONIKA_CMD_TEXT` en `.env` y `systemctl restart python-gps-redirect-gprs.service`.

## Licencia

Sin licencia especificada.
