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
already run (see `.env.example`). The background discovery loop only ever
reads from your MikroTik router; writes only happen from an explicit,
admin-triggered click (see "Group → MikroTik enforcement" below) — never
from a timer.

## Foundation (shipped)

- Device registry (`devices` table) auto-populated from MikroTik's DHCP
  leases, hotspot active sessions, hotspot hosts, and ip-binding table —
  merged by MAC address every `SYNC_INTERVAL_S` (default 60s).
- Two seeded groups: **Liberado** (free, default for new devices) and
  **Hotspot** (requires MikroTik hotspot login).
- Admin panel: browse/search/filter devices, see online status, reassign a
  device's group or linked user, add notes.
- Family member records (`users` — name/email/birthdate, not login
  accounts) that a device can be linked to.
- `/health` endpoint for uptime monitoring.

## Group → MikroTik enforcement (shipped)

A device's group now actually means something: `app/enforcement.py`
computes, purely from data the discovery loop already keeps fresh, whether
a device's real MikroTik state (`mikrotik_bound`/`mikrotik_bypassed`)
matches what its group calls for. The device detail page shows that as
"✓ in sync" or "⚠ needs: binding will be created/removed" with an **Apply
to MikroTik** button — every application is one explicit click
(`POST /devices/{mac}/apply-mikrotik`, `app/mikrotik_enforce.py`), logged
to `enforcement_log` (success/failure + MikroTik's response), never
automatic. `/enforcement` lists every device currently out of sync across
the whole registry, read-only, with no bulk-apply button — that's a
deliberate choice while trust in this feature is still being built; each
change should be a decision, not a batch job. `Liberado` devices get no
MikroTik entry at all (they fall through the router's existing subnet-wide
bypass); `Hotspot` devices get a MAC-keyed `ip-binding` with no `type`
(comment `familink`), the same shape the pre-existing hand-managed entries
already used.

**This makes the missing admin-panel auth (below) materially more
important** — anyone on the LAN can now flip a device's actual internet
access, not just a database label.

## Admin panel auth (shipped)

HTTP Basic Auth (`app/auth.py`), applied to every route except `/health`
(so uptime monitors keep working unauthenticated) and `/static/*`. Set
`ADMIN_USER`/`ADMIN_PASSWORD` in `.env`; leaving `ADMIN_PASSWORD` empty
disables enforcement entirely (fine for local dev) but logs a loud warning
at startup every time so an operator can't accidentally ship it open
without noticing.

## Port scanner (shipped)

`app/portscan.py` runs an nmap TCP-connect scan (`-sT`, no special
capabilities needed in a container) against a curated port list — cameras
(554/8899/34567/37777), printers (9100/631), common web/IoT ports, a few
well-known services — the first time the discovery loop sees a brand new
device (never re-triggered automatically after that). Results land in
`device_scan_results`; a simple first-match heuristic
(`app/portscan.py:guess_type`) turns the open-port set into a human label
(e.g. "Camera (ONVIF)", "Printer (JetDirect)") stored on
`device.vendor_guess` if it's still empty. The device detail page shows the
scan table and a manual **Rescan** button for anyone who wants a fresh read
without waiting for a device to look "new" again.

As part of this, fixed a real gap in IP discovery: `current_ip` used to
come only from MikroTik's DHCP lease table, which misses any device with a
manually-configured static IP outside the DHCP pool (confirmed live — a
camera with DHCP disabled on its network config never appears in `lease` at
all). `merge_mikrotik_views` now also takes `address` from
`/ip/hotspot/active` and `/ip/hotspot/host`, which reflect whatever
MikroTik currently sees on the wire (ARP-level) regardless of whether the
device ever requested a DHCP lease.

## MQTT presence publisher / Home Assistant discovery (shipped)

`app/mqtt_publish.py` runs once per discovery cycle, right after devices
are refreshed. Entirely opt-in — a no-op unless `MQTT_HOST` is set. For
every device: publishes its online/offline state (retained) to
`<MQTT_TOPIC_PREFIX>/<object_id>/state`, and — once per device, tracked via
`device_mqtt_state.discovery_published_at` so it's never resent — a
[Home Assistant MQTT Discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery)
config (retained) to `homeassistant/binary_sensor/<object_id>/config`,
`device_class: connectivity`. `object_id` is `familink_<mac_with_underscores>`,
stored on `device_mqtt_state.object_id`. DB reads/writes go through
`asyncio.to_thread` (same pattern as `app/sync.py`) rather than mixing sync
SQLAlchemy calls into the async MQTT client loop directly.

Verified live end-to-end against the real EMQX broker (dedicated
`familink` MQTT user, not reusing Home Assistant's own credential) — a
retained discovery config and state both landed correctly and matched
HA's expected schema.

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

### Bulk-apply on the /enforcement page
Today every change requires opening the device and clicking Apply
individually, by design (see above). Once the enforcement feature has run
correctly for a while, a reviewed "apply all pending" action on
`/enforcement` would remove that friction for routine cleanup.

## Non-goals

familink is not trying to replace MikroTik's firewall or hotspot engine —
it's a management layer on top of it. It also isn't a general network
monitoring tool (no bandwidth graphs, no packet inspection); it answers
"what is this device and what class of access does it get," nothing more.
