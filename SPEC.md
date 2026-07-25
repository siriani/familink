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
