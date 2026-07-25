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

**Real bug found live (23/jul/2026): RouterOS evaluates `ip-binding`
top-to-bottom, first match wins — like an ordered firewall chain, not a
set matched by specificity.** A freshly created MAC-specific binding lands
at the *end* of the list by default. If a broad catch-all bypass rule
(e.g. a subnet-wide `192.168.1.0/24` bypass predating familink) sits
earlier in the list, it silently wins over the new, more specific
MAC binding — the device stays bypassed even though familink correctly
set it to require login. This made every `Hotspot`-group enforcement
familink had applied so far a no-op in practice, without any error
surfacing anywhere (the write succeeded; RouterOS just never consulted
it). Fixed with `MikroTikClient.move_to_top()` — after creating or fixing
a binding, it's moved to sit before whatever is currently first in the
list, so it's always evaluated ahead of any pre-existing catch-all.
Called from both `app/mikrotik_enforce.py`'s `_create_or_fix_binding` and
`app/mikrotik_quota.py`'s `block_device` — the same class of bug would
otherwise make a quota block equally silent. Retroactively re-applied to
the bindings already created before this fix shipped.

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

## Fingerbank device identification (shipped)

Optional enrichment on top of the port scanner's guess, using
[fingerbank.org](https://fingerbank.org/)'s device-combinations database —
`app/fingerbank.py:lookup_mac` queries `GET /api/v2/combinations/
interrogate` by MAC address and stores `device_name`/`manufacturer`/
`score` onto the device row (`device.fingerbank_*`, migrations/versions/
0007_fingerbank.py). Triggers the same way the port scanner does: once
automatically the first time the discovery loop sees a brand new device
(`app/sync.py`), plus a manual **Refresh identification** button on the
device detail page for anyone who wants a fresh read. Shown as its own
section on that page, not merged into `vendor_guess` — the two are
different signals (Fingerbank's OUI/device database vs. the port-scanner's
hand-picked port heuristics) and can legitimately disagree.

**MAC-only, not full DHCP fingerprinting.** Fingerbank's API also accepts
a `dhcp_fingerprint` (option 55 parameter-request list) and `dhcp_vendor`
for much more precise device identification, but MikroTik's REST API
doesn't expose that raw data today — only what's already in `/ip/
dhcp-server/lease` (address, hostname, a handful of DHCP-relay fields),
none of which is the option-55 list itself. A MAC-only lookup still beats
the port scanner's heuristics for manufacturer identification (a real
vendor database vs. a dozen hand-picked ports) but can't distinguish two
devices from the same manufacturer the way a full fingerprint could.
Worth revisiting if a future RouterOS version exposes that data via REST.

**API key is admin-configured at `/settings`, not an env var.** Every
other credential in familink (MikroTik password, DB password, MQTT
password) is `.env` + redeploy — deliberately different here, because
familink is community software now and a non-technical admin running
someone else's install may not have shell access to edit `.env` at all.
`app/models.py:Setting` is a minimal key/value table for exactly this
case, not a general settings framework — a second admin-configurable
value would get its own row under the same table, not a new schema
concept. No key configured = lookups are a fast no-op (never a failed
network call, never a startup warning) — this is opt-in, not everyone
wants to send their family's device MACs to a third-party service.

## MQTT presence publisher (shipped)

`app/mqtt_publish.py` runs once per discovery cycle, right after devices
are refreshed. This module is deliberately **generic MQTT, not a Home
Assistant integration** — it publishes plain retained JSON to its own
topics and knows nothing about HA, Node-RED, or any other consumer.
Earlier revisions implemented HA's MQTT Discovery convention
(`homeassistant/.../config` topics, `device_class`, `payload_on`/`off`,
an `origin` block) directly in this module; that coupling was removed
25/jul/2026 — familink's job is to publish presence, not to know what
Home Assistant's discovery schema looks like. If you want entities in
HA, point its generic MQTT integration at the topics below by hand (or
point anything else at them — that's the point of keeping this plain).

Opt-in at two levels:

- **Broker connection** (host/port/user/password/topic prefix) is
  admin-configured at `/settings`, not `MQTT_*` env vars — same reasoning
  as the Fingerbank key: a non-technical admin running someone else's
  install may not have shell access to edit `.env`. No host configured =
  `publish_all()` is a no-op.
- **Per device/person**: `device.mqtt_enabled` / `user.mqtt_enabled`
  (both default false, toggled from that device's/person's own detail
  page). A family's whole device list showing up on the broker without
  being asked is exactly the kind of silent-by-default behavior familink
  avoids elsewhere (see the MikroTik-write discipline, enforcement's
  explicit-click-only design). Only opted-in rows are loaded or
  published.

**Topics** (all retained): `<prefix>/bridge/state` — `"online"` while
familink is connected, set as the MQTT client's Last Will (`"offline"`)
so a subscriber can tell familink itself is down, independent of any one
device's state going stale (same idea as Zigbee2MQTT's bridge/state
topic, but that's a generic MQTT-bridge pattern, not HA-specific). For
each opted-in device, `<prefix>/<object_id>/state` — a JSON object:
`online`, `mac`, `hostname`, `ip`, `manufacturer`/`model` (from
Fingerbank enrichment when available, falling back to the port
scanner's `vendor_guess`), `last_seen`. For each opted-in person,
`<prefix>/familink_user_<id>/state` — `online` (true if any of their
devices is online), `name`, `devices_online` (count). `object_id` is
`familink_<mac_with_underscores>` for devices, the numeric id (not the
name, so it stays stable if someone's renamed) for people.

**Disabling isn't silent.** A retained MQTT message sits on the broker
forever until something overwrites or clears it — so turning
`mqtt_enabled` off doesn't make a subscriber's last-known state
disappear on its own. `device_mqtt_state`/`user_mqtt_state` track which
rows currently have a retained topic published; every cycle, any row
that's no longer enabled but still has one gets an empty retained
payload (the standard MQTT way to clear a retained topic) and its
tracking row deleted, so re-enabling later republishes fresh instead of
assuming stale state still applies.

Verified end-to-end with a mocked MQTT client (real code path, fake
transport): opt-in filtering, the availability/Last-Will topic, and the
disable → clear-the-retained-topic path all confirmed. An earlier
revision (the since-removed HA Discovery version) was verified live
against a real EMQX broker; worth a live re-check of this revision next
time the real broker is reachable.

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
  ip-binding (`app/mikrotik_quota.py:block_device`). **Updated
  24/jul/2026**: quota and hotspot-enforcement bindings used to carry two
  separate comment tags (`familink-quota` vs `familink`) so the two
  subsystems wouldn't touch each other's bindings — replaced by a single
  unified owner, see "Captive portal self-registration" below;
  `block_device`/`unblock_device` are now thin callers of
  `app/mikrotik_binding.py:apply_binding_state`, same as enforcement. Once daily
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

## Captive portal self-registration (shipped)

Replaces MikroTik's own hotspot login page entirely — a `Hotspot`-group
device that isn't yet linked to a `User` no longer sees a MikroTik
`/ip hotspot user` username/password form. The router's
`hotspot/login.html` (one-time, out-of-band router-side change, see
`mikrotik-hotspot-html/README.md`) now redirects to familink's own
`GET/POST /captive` (`app/routers/captive.py`), a standalone page (no
admin nav — this is shown on family members' own phones) with an
identity picker (existing `users` by name, or "create new"). Requires a
`/ip hotspot walled-garden ip` entry so the unauthenticated redirect can
even reach `192.168.1.10:8190` in the first place before it's allowed
to load anything else.

**Never trusts the `mac` query param for writes** — every request
re-resolves the true MAC live from MikroTik's own `ip/hotspot/active`/
`ip/hotspot/host` tables, keyed by the actual connecting IP
(`request.client.host`), not the possibly-stale `devices.current_ip`.
This matters because `/captive` is, by necessity, the one unauthenticated
route in the whole app (`app/auth.py`'s `_EXEMPT_PATHS`) — without the
live re-resolution, anyone on the LAN could craft `/captive?mac=<victim>`
themselves. `POST /captive` also refuses to touch a device that already
has a `user_id` (reassigning an already-registered device goes through
the authenticated admin Owner dropdown instead, not this public
endpoint).

**Binding ownership unification, the part of this that wasn't just "add
a page":** a `Hotspot`-group device goes through one lifecycle on the
same MikroTik ip-binding — not-yet-identified (forced through `/captive`)
→ identified + under quota (free access) → quota exhausted (blocked) →
next day (free again, no re-identifying). The pre-existing code couldn't
express that (enforcement and quota each assumed the other's bindings
were foreign and refused to touch them, using two different comment
tags). `app/enforcement.py:desired_binding_state(device)` is now the one
pure function computing what a device's binding *should* be
(`none`/`regular`/`bypassed`/`blocked`), and
`app/mikrotik_binding.py:apply_binding_state` is the one function that
actually reads/writes it — `app/mikrotik_enforce.py`, `app/mikrotik_quota.py`,
and `app/routers/captive.py` are all thin callers now, all using the same
`comment=familink` tag.

**Security gap found live (25/jul/2026):** the "I already have an account"
picker originally let anyone tap an existing person's name to link a new
device to them — zero verification. On a home LAN that's mostly benign
(family members trust each other), but the same picker is reachable by
anyone who joins the Wi-Fi, guest included, and it silently reassigns
whatever access class (including an unlimited/bypassed adult account) to
whoever clicks. Fixed with a 4-digit PIN per person (`User.pin_hash`,
`app/pin.py` — PBKDF2-SHA256, stdlib only, no new dependency): the picker
now only lists people who have a PIN set (`app/routers/captive.py:
_pin_selectable_users`), and selecting one requires it. A person with no
PIN yet is simply invisible on `/captive` until an admin sets one from
their edit page — not a hard failure, just excluded, so migrating a
pre-existing family (all starting with `pin_hash=NULL`, see migrations/
versions/0006_user_pin.py) doesn't require touching every row at once.
Self-registering via "Create account" requires setting a PIN in the same
step, so new accounts are never left unprotected. Failed attempts are
rate-limited in-process, keyed by *target user id* (not device/IP, which a
LAN attacker can trivially spoof) — 5 wrong guesses locks that person out
of the picker for 15 minutes, reset on a correct guess. Deliberately not a
general login/session system (see the `User` model docstring) — this PIN
protects exactly one action (linking a new device to an existing person)
and nothing else about familink's admin surface.

**Real gap found live (24/jul/2026), not anticipated by the design above:**
several `Hotspot`-group family devices already had a MikroTik ip-binding
predating familink entirely (hand-created by the retired hotspot-admin
script, comment = the device's own hostname, e.g. `Joao-nightpudim`,
`Isis`, `Bia-Xiaomi`). `apply_binding_state` correctly refuses to touch a
binding it doesn't own (logged as `"device already has an unrelated
ip-binding ... not touching it"`) — which is the right call to avoid
silently clobbering something unrelated, but it meant these specific
devices were never actually brought under familink's control: some had
already self-identified via `/captive` (the DB said so) while the router
still silently bypassed them on a stale binding; others had never even
reached the captive redirect, since the old binding's default `bypassed`-
equivalent behavior meant they never hit the hotspot wall in the first
place. Fixed per-device: deleted the stale binding, then
`POST /devices/{mac}/apply-mikrotik` to let familink create a fresh
`comment=familink` one in the correct state. No code change needed — the
"remove or retag manually" the code already logged was exactly right;
this was a one-time cleanup of pre-existing router state, not a bug in
`apply_binding_state` itself. Worth checking for again if any other
legacy (non-`familink`-tagged) `ip-binding` entries turn up later
(`GET /ip/hotspot/ip-binding`, filter out anything not commented
`familink` and cross-reference the MAC against `devices`).

Old MikroTik-native `/ip hotspot user` accounts (`bia`/`joao`/`isis`/
`tati`/`allan`/`visita`) are **not yet deactivated** — `tati` and `allan`
had live sessions with real traffic when this was last checked, so
disabling them was deliberately left for a moment when someone's actually
around to notice if a device unexpectedly loses access. Deactivate (not
delete) once confirmed nothing still depends on them.

## Internationalization (shipped)

familink shifted from a personal project to open-source community
software, so the UI (admin panel + `/captive`) is now translated into
pt-BR, English, Spanish, German and Simplified Chinese, via standard
gettext/Babel — `.po`/`.pot`, not a bespoke format, because that's what
community translation tooling (Weblate, Poedit, Crowdin) already speaks.

Templates call plain `_()`/`ngettext()`/`pgettext()` as ordinary Jinja2
context variables (`{{ _('Save') }}`), not `{% trans %}`/`jinja2.ext.i18n`
— that extension's newstyle-gettext support leans on private Jinja
internals. Plain context variables are public API, per-request-safe (each
`TemplateResponse()` call gets its own translations via
`fastapi.templating.Jinja2Templates`'s `context_processors`, never mutating
the shared `Environment`), and still fully discovered by `pybabel
extract`'s AST scanner.

Locale resolution is two different mechanisms, deliberately not unified
(`app/i18n.py`'s `LocaleMiddleware`, registered after `BasicAuthMiddleware`
so it still runs on a 401): the admin panel uses `?lang=xx` → a
`familink_lang` cookie → `DISPLAY_LANGUAGE` env var (default `pt-BR`);
`/captive` — a visitor's own device, never logged in — always parses their
browser's `Accept-Language` header instead, since a cookie/query-param flow
makes no sense for someone who just joined the network.

**The `device.vendor_guess` migration.** `app/portscan.py`'s port-based
type guesser used to persist the literal English display label directly
onto the device row (`"Camera (ONVIF)"`) — great for a single-language app,
impossible to translate afterward, since the value *is* the English text.
It now stores a stable key (`camera_onvif`) and looks up/translates the
label at render time (`TYPE_LABEL_MSGIDS`, `app/routers/devices.py`);
`migrations/versions/0005_vendor_guess_keys.py` backfills the 13
previously-stored English values to their keys with a deterministic
`UPDATE ... WHERE vendor_guess = '<old label>'` per value — safe because
`vendor_guess` is only ever written by the scanner, never hand-edited via
any form.

**Deliberately not translated:** `EnforcementLog.detail`/`QuotaLog.detail`
(the "recent attempts" log tables) — 100% English today, developer-authored,
and interpolate raw MikroTik HTTP responses and Python exception text; this
is audit/debug output, not UI chrome, and not meaningfully translatable
without losing the actual diagnostic content. Same reasoning for
`DeviceScanResult.service_guess` (nmap's own raw text) and JSON API error
bodies (`HTTPException` details in `devices.py`/`groups.py`/`users.py`,
never template-rendered). The `"RESTRITO"`/`"23:00"`/`"05:00"` placeholders
in the group form are literal example *values* for a MikroTik address-list
name and HH:MM fields — format is language-invariant, left as-is.

Tone/register (informal vs. formal address in es/de) and the MikroTik-jargon
strings ("MikroTik ip-binding", the enforcement action labels) got a
first-pass translation but are flagged for a native-speaker review pass,
not treated as final.

## Roadmap — not built yet

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
