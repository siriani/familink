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

## Quota / schedule engine (shipped)

Replaces the hand-written MikroTik scripts entirely — investigated the
live router before designing this: quota (`limit-uptime`/`uptime` on a
MikroTik hotspot **user**, reset nightly) and night block (a firewall rule
matching a **static** address-list, completely independent of hotspot
login) turned out to be two unrelated mechanisms, and the night-block list
was already stale for any device whose MAC had rotated (phones do this
periodically for privacy) since it was IP-keyed and never updated.

**Quota accounting lives entirely in familink, not MikroTik** (a
deliberate design choice, not the first draft — the first version pushed
`limit-uptime` to a MikroTik hotspot user like the retired script did;
switched after building and testing that version, because it required
every quota-tracked person to have an existing MikroTik hotspot login and
only worked for `hotspot_required` groups). MikroTik's role is reduced to
a plain block/unblock per device — no login, no session, no counter on the
router at all:

- **Quota**: fully independent of `hotspot_required`. Every discovery
  cycle (`app/quota.py:tick_and_enforce`), for each `users` row with an
  applicable quota (group default via `app/quota.py:applicable_group`, or
  a personal override on the `users` row itself — override wins), if any
  of their linked devices is online, `seconds_used_today` gets
  `SYNC_INTERVAL_S` added. The moment it reaches `todays_limit_s(user)`,
  every device linked to that person gets a `type=blocked` MikroTik
  ip-binding (`app/mikrotik_quota.py:block_device`, comment
  `familink-quota` — a different tag than `app/mikrotik_enforce.py`'s
  `familink`, so the two systems never touch each other's bindings; don't
  mix a quota group with a `hotspot_required` group for the same device,
  see `block_device`'s docstring for what happens if you do). Once daily
  at 00:01 `DISPLAY_TIMEZONE` (`app/quota.py:nightly_reset_loop`), every
  user's counter zeroes and anyone blocked gets unblocked
  (`unblock_device`) — logged either way. `/users/{id}/reset-today` is a
  manual early-unblock for "give them extra time today" without waiting
  for midnight, same idea as the retired hotspot-admin panel's old "Reset"
  button.
- **Night block**: deliberately independent of quota too — operates on
  every device in a group directly, by `current_ip`, so a group like
  "TV/Playstation" can curfew at a fixed hour with zero login or quota
  involved. Every discovery cycle, familink reconciles `comment=familink`
  entries in the group's `night_block_address_list` to match current
  device IPs (`app/mikrotik_quota.py:sync_night_block`) — never touches
  entries with any other comment, so a reused list (e.g. the original
  `RESTRITO`) keeps whatever else was already in it. familink never
  creates the firewall filter rule that actually drops traffic during the
  window — that's set up once on the router, same shape as the
  pre-existing rules, and familink is just told which address-list to keep
  in sync.

Groups aren't limited to the 2 seeded ones — full CRUD at `/groups`
(`daily_limit_weekday_s`/`daily_limit_weekend_s`/`night_block_start`/
`night_block_end`/`night_block_address_list`). People are managed at
`/users` (name/email/birthdate, personal quota override, read-only
today's-usage/blocked status).

## Roadmap — not built yet

### Captive portal self-registration
User connects to Wi-Fi, hits a MikroTik hotspot walled-garden landing page
that talks to familink instead of (or in addition to) MikroTik's built-in
login, registers name/email/birthdate into `users`, links the connecting
device's MAC. Uses the already-provisioned `registration_tokens` table for
the linking handshake.

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
