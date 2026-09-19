# AGENTS.md

Guía de contexto y convenciones para sesiones de trabajo sobre `python-gps-redirect-gprs`.

## Propósito del repositorio

Conjunto de interfaces en Python que levantan **puertos TCP** para que dispositivos **GPS compatibles** (Teltonika y otros) se conecten. Al recibir tramas (incluyendo comandos GPRS), el servidor responde con paquetes codificados (codec 12 de Teltonika) que **redirigen** al dispositivo hacia otro servidor (IP/puerto). En esencia: un *redirector* GPS.

## Estructura actual

```
python-gps-redirect-gprs/
├── _config.py              # Loader de .env sin dependencias externas
├── .env.example            # Plantilla versionada (NO persistir .env real)
├── teltonika.py            # Servidor TCP en puerto 37540 para dispositivos Teltonika
├── logs/                   # (generado) teltonika.log y teltonika_imei.log
├── docs/                   # Documentación de codecs por fabricante (no versionada)
└── README.md
```

Cada fabricante/modelo de GPS se atenderá con su propio archivo (`teltonika.py`, futuros `concox.py`, `gt06.py`, etc.).

## Convenciones

- **Un archivo por fabricante/modelo**, ejecutable standalone (`python3 <fabricante>.py`).
- **Encoding de paquetes GPS**: cada fabricante usa su propio codec (Teltonika usa codec 12 / CRC-16/IBM).
- **Protocolos**: cada fabricante define qué transporte acepta. Teltonika maneja TCP y UDP en paralelo, en el mismo puerto por defecto (37540). El kernel lo permite porque TCP y UDP son L4 distintos.
- **Puerto**: cada archivo declara `TCP_PORT` y `UDP_PORT` desde la config. Teltonika = 37540 para ambos.
- **Comando de redirección**: constante `COMMAND_TEXT` con el formato nativo del fabricante (`setparam 2004:IP;2005:PUERTO;2006:0` para Teltonika). El server es un **redirector puro**: ante cualquier trama Teltonika válida (TCP o UDP), responde con `COMMAND_TEXT` codificado en Codec 12. No procesa telemetría.
- **Semántica de re-envío (TCP y UDP)**: tras el primer paquete de un IMEI → `setparam`; los paquetes subsiguientes del mismo IMEI → `cpureset` (Codec 12 reset). Por eso un dispositivo que re-envía la misma trama N veces produce N `cpureset` después del `setparam` inicial. Esto es by design: garantiza que el dispositivo tome la configuración aunque ignore la primera respuesta.
- **Logging**: cada script genera un `<nombre>.log` con timestamp en zona horaria UTC-5. Adicionalmente, `<nombre>_imei.log` registra **una línea por IMEI único** (TCP/UDP), útil para contabilizar cuántos dispositivos se conectaron.
- **Concurrencia**: jerarquía de procesos no-daemon. `main()` lanza los loops de servidor (`tcp_server`, `handle_udp_server`); los loops a su vez lanzan `handle_client` por conexión TCP. UDP se mantiene en un solo loop con `recvfrom`.
- **Timeout de lectura por socket**: 300s, configurable. Funciona como watchdog de inactividad por conexión: si vence sin tráfico en `handle_client`, se loguea `TCP idle timeout, closing connection: <addr>` y se cierra **esa conexión TCP remota** (`conn.close()`). El socket de escucha en `tcp_server` permanece abierto aceptando nuevas conexiones en `TELTONIKA_TCP_PORT`; el proceso listener **no se cierra ni muere por inactividad**. Comportamiento análogo en UDP: el `recvfrom` vence, se loguea `UDP idle timeout (still listening)` y el loop continúa (UDP no tiene conexión persistente que cerrar).
- **Sin comentarios en el código** salvo que el usuario lo pida explícitamente.

## Configuración

La configuración se lee de un único archivo `.env` en la raíz (NO versionado, ver `.gitignore`). Se carga vía `_config.load_env(prefix, defaults)` en cada script. Orden de resolución:

1. Variable de entorno del sistema (mayor prioridad).
2. Entrada en `.env` cuyo nombre empieza con `prefix`.
3. Valor por defecto pasado a `load_env`.

Convenciones de nombres:
- `<FABRICANTE>_TCP_PORT` — puerto TCP donde escucha el script.
- `<FABRICANTE>_UDP_PORT` — puerto UDP donde escucha el script (mismo número que TCP por defecto; distintos protocolos L4).
- `<FABRICANTE>_CMD_TEXT` — texto del comando GPRS de redirección.
- `<FABRICANTE>_SOCKET_TIMEOUT` — segundos de espera por `recv`/`recvfrom`.

Para iniciar: `cp .env.example .env` y editar valores. `.env.example` sí está versionado.

## Funciones comunes reutilizables (referencia desde `teltonika.py`)

- `_reflect(value, width)` — inversión de bits.
- `crc16_ibm(data)` — CRC-16/IBM con reflexión entrada/salida, polinomio 0x8005.
- `make_teltonika_cmd(cmd_str)` — arma un paquete codec 12 Teltonika (zeros 4B + datasize 4B + codec 1B + qty 1B + cmd_type 1B + cmd_size 4B + contenido + qty2 1B + CRC 4B).
- `make_teltonika_udp_ack(avl_packet_id, count)` — arma el ACK UDP Teltonika de 7 bytes (length 2B + id 2B + type 1B + avlId 1B + count 1B). **Actualmente sin uso** en el flujo del redirector (se mantiene como referencia).
- `parse_udp_header(data)` — parsea header UDP Teltonika (length 2B + packetId 2B + type 1B + avlId 1B + imeiLen 2B + imei). Devuelve dict o None.
- `log_message(addr, rx_hex, rx_decoded, tx_hex)` — append a `<script>.log`. Blindado con try/except para no matar el proceso ante fallos de disco.
- `log_imei_once(seen, proto, imei, ttl=86400.0)` — append a `<script>_imei.log` solo si el IMEI no está en el dict `seen`. `seen` es `{imei: timestamp}` (mutado in-place); TTL por defecto 24h, purgado en cada llamada. En TCP vive por conexión; en UDP global al loop.
- `handle_client(conn, addr)` — loop RX/decodificar/enviar respuesta en proceso hijo (TCP). Mantiene `redirected_imei: set[imei]` (keyed por IMEI) en la conexión: el primer paquete de un IMEI handshake (17B con IMEI parseable) responde con `make_teltonika_cmd(COMMAND_TEXT)` (Codec 12 `setparam`); los siguientes con `make_teltonika_cmd("cpureset")` (Codec 12 reset). Tramas sin IMEI parseable también caen en `cpureset`. Log de IMEI único independiente: `seen_imei: set`.
- `tcp_server(host, port)` — accept loop TCP, lanza `handle_client` por conexión.
- `handle_udp_server(host, port)` — loop único de `recvfrom`. Mantiene `redirected: dict[imei, bool]` keyed por IMEI: el primer paquete válido de un IMEI responde con `make_teltonika_cmd(COMMAND_TEXT)` (Codec 12 `setparam`); los siguientes con `make_teltonika_cmd("cpureset")` (Codec 12 reset). Header inválido no responde ni muta estado. La key por IMEI (no por `addr`) resiste NAT rebind.

## Cómo agregar un nuevo fabricante

1. Crear `<fabricante>.py` copiando la estructura base de `teltonika.py`.
2. Implementar el constructor de comandos específico (cada fabricante tiene su protocolo).
3. Ajustar `LISTEN_PORT`, `COMMAND_TEXT` y la lógica de detección de "handshake vs datos" en `handle_client`.
4. Agregar las variables `<FABRICANTE>_TCP_PORT`, `<FABRICANTE>_CMD_TEXT`, `<FABRICANTE>_SOCKET_TIMEOUT` a `.env.example`.
5. Probar levantando el script y conectando un dispositivo o un simulador TCP.

## Verificación

No hay suite de tests automatizados aún. Verificación manual:
- `python3 teltonika.py` y conectar vía `nc localhost 37540` o con un dispositivo real.
- Inspeccionar `<script>.log` para confirmar RX → TX.

## Zona horaria

Toda la actividad se registra en **UTC-5** (`UTC_MINUS_5`).

## Release a producción — `master@6e56b8d`

El proyecto queda listo para producción como **redirector puro Teltonika TCP/UDP**:

- **Logs durables**: en `logs/` (no raíz), con blindaje ante fallos de disco.
- **Memoria acotada**: dicts IMEI-keyed con TTL 24h en UDP; TCP se libera al cerrar la conexión.
- **Watchdog robusto**: timeout no cierra sockets; server siempre disponible.
- **Cambio de destino**: editar `TELTONIKA_CMD_TEXT` en `.env` y reiniciar (`systemctl restart python-gps-redirect-gprs.service`).
- **Despliegue soportado**: systemd unit en `README.md` (sin Docker — el proyecto es Python puro y corre directo en el VPS con `python3 teltonika.py`).
- **Sin dependencias externas**: solo `python3` con stdlib (sin `pip install`).

### Comportamiento operativo verificado

Server fresco recibe `RX #1 [IMEI X] → setparam` y `RX #2 [mismo IMEI] → cpureset`. Re-envíos del mismo dispositivo producen `cpureset` por diseño, no son bug.

### Pendiente / fuera de scope

- No hay suite de tests automatizados. Verificación es manual con Packet Sender.
- Hindsight memory backend cayó (Postgres shared memory). `AGENTS.md` y `docs/` quedan como fuente durable hasta que vuelva.