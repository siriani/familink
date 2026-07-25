# MikroTik hotspot HTML — captive portal redirect

`login.html` here replaces the router's own `hotspot/login.html` — instead
of showing a username/password form against a MikroTik `/ip hotspot user`
account, it redirects (meta-refresh + a visible fallback button, since
some captive-portal mini-browsers don't reliably run JS or follow
meta-refresh across origins) to familink's own `/captive` page, carrying
`$(mac-esc)` and `$(link-orig-esc)` as query params (UX hints only —
`app/routers/captive.py` never trusts them for a write, it re-resolves the
real MAC live from MikroTik's own hotspot tables).

## Prerequisites (verify live, don't assume)

1. **Walled-garden entry** so an unauthenticated client can even reach
   familink's host before being redirected:
   ```
   /ip hotspot walled-garden ip add action=accept dst-address=192.168.1.10 \
     dst-port=8190 protocol=tcp comment=familink-captive
   ```
   Without this, the redirect itself gets blocked — verified live
   (23/jul/2026): an unauthenticated hotspot-gated device can reach
   `192.168.1.10:8190` with this entry in place, and gets blocked (even
   from the router's own `/rest` API on port 80) without it.
2. `familink` must already be reachable at `http://192.168.1.10:8190` from
   every device on the LAN (it is, by default — no VPN/proxy involved).

## Installing

```bash
# Back up the router's current file first
scp admin@192.168.1.1:hotspot/login.html login.html.orig-backup

# Push the new one
scp login.html admin@192.168.1.1:hotspot/login.html
```

`alogin.html` (RouterOS's own "your time is up" page, shown when a MikroTik
hotspot *user*'s `limit-uptime` session expires) is intentionally left
untouched — familink's quota engine doesn't use MikroTik hotspot user
accounts or `limit-uptime` at all (see SPEC.md), so this page simply goes
unused once the old `/ip hotspot user` accounts are deactivated.

## Rolling back

```bash
scp login.html.orig-backup admin@192.168.1.1:hotspot/login.html
```
