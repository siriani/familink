# familink — spec & roadmap

## Vision

Most home networks end up with a hand-maintained pile of MikroTik firewall
rules and ip-bindings to answer one question: *what is this device, whose is
it, and should it have to log in before it gets internet?* familink turns
that into a proper device registry: it watches your MikroTik router, keeps a
database of every device that's ever shown up, and gives you a simple UI to
decide, per device, whether it's free ("Liberado") or has to pass through
the hotspot ("Hotspot"). New devices are always free by default — so a
random ESP32 sensor or a smart plug never gets accidentally locked out
behind a login page.

## Architecture

```
 MikroTik RouterOS (REST API)
        |  read-only poll every SYNC_INTERVAL_S
        v
   familink app (FastAPI) <----> MariaDB (bring your own)
        |
        v
   Admin UI (Jinja2, plain HTML/CSS, no build step)
```

familink does not bundle a database — point it at any MariaDB/MySQL you
already run (see `.env.example`). It never writes to your MikroTik router in
the foundation phase; it only reads.

## Foundation (shipped)

- Device registry (`devices` table) auto-populated from MikroTik's DHCP
  leases, hotspot active sessions, hotspot hosts, and ip-binding table —
  merged by MAC address every `SYNC_INTERVAL_S` (default 60s).
- Two seeded groups: **Liberado** (free, default for new devices) and
  **Hotspot** (requires MikroTik hotspot login) — informational only in
  this phase, nothing enforces them yet.
- Admin panel: browse/search/filter devices, see online status, reassign a
  device's group or linked user, add notes. All writes are database-only.
- Family member records (`users` — name/email/birthdate, not login
  accounts) that a device can be linked to.
- `/health` endpoint for uptime monitoring.

## Roadmap — not built yet

### Group → MikroTik enforcement sync
Push `devices.group_id` changes made in the admin panel back to MikroTik:
create/update/delete `/ip/hotspot/ip-binding` entries so `hotspot_required`
actually takes effect on the router, replacing the manual Winbox workflow.
Needs a write-capable path (already stubbed in `app/mikrotik.py`, unused)
and a reconciliation strategy for entries that exist on MikroTik but aren't
in familink yet (first-sync import).

### Port scanner
Background/on-demand nmap scan per device IP, writing rows into the
already-provisioned `device_scan_results` table, to help identify "mystery"
devices in the admin UI (service guess/banner surfaced on the device detail
page).

### MQTT presence publisher / Home Assistant discovery
Publish [Home Assistant MQTT Discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery)
configs to `homeassistant/binary_sensor/<object_id>/config` per device
(online/offline) plus a state topic, using the already-provisioned
`device_mqtt_state` table to track what's been published and avoid
re-publishing discovery configs every cycle.

### Captive portal self-registration
User connects to Wi-Fi, hits a MikroTik hotspot walled-garden landing page
that talks to familink instead of (or in addition to) MikroTik's built-in
login, registers name/email/birthdate into `users`, links the connecting
device's MAC. Uses the already-provisioned `registration_tokens` table for
the linking handshake.

### Quota / schedule engine, generalized per-group
Express quota rules (e.g. "3h on weekdays, 8h on weekends, blocked 11pm–5am")
per-group in familink's database and push them to MikroTik hotspot user
profiles, instead of hand-coding them against specific usernames on the
router.

### Admin panel auth
Foundation ships with no authentication (LAN-only assumption). Add at
minimum a shared admin credential (Basic Auth via env var) before running
this on anything less trusted than a home LAN.

## Non-goals

familink is not trying to replace MikroTik's firewall or hotspot engine —
it's a management layer on top of it. It also isn't a general network
monitoring tool (no bandwidth graphs, no packet inspection); it answers
"what is this device and what class of access does it get," nothing more.
