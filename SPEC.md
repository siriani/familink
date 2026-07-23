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

## Roadmap — not built yet

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
Still no authentication (LAN-only assumption) — now the highest-priority
item on this list, since the enforcement feature above means an
unauthenticated visitor on the LAN can change what a device's internet
access actually is, not just relabel it in a database. Add at minimum a
shared admin credential (Basic Auth via env var) before running this on
anything less trusted than a home LAN.

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
