# familink

A device registry for home networks with a MikroTik router. It watches your
router, keeps a database of every device that's ever shown up, and gives
you a simple admin UI to see who's online and decide whether each device is
free ("Liberado") or should have to log in through the hotspot ("Hotspot").
New devices always default to free, so IoT/sensor gear never gets
accidentally locked out.

The background sync that discovers devices is always read-only. Changing
what's actually enforced on your router is a separate, deliberate step: the
device page shows whether MikroTik matches the assigned group, and an
**Apply to MikroTik** button applies it — one explicit click at a time,
logged, never automatic. See [SPEC.md](SPEC.md) for the full vision and
what's still on the roadmap (port scanning, MQTT/Home Assistant presence
publishing, a captive-portal self-registration flow).

## Requirements

- A MikroTik router (RouterOS 7+) with the REST API enabled (on by default
  on the `www` service) and an admin account it can use.
- A MariaDB or MySQL server — familink doesn't bundle one, bring your own.
- Docker + Docker Compose.

## Quick start

```bash
git clone https://github.com/siriani/familink.git
cd familink
cp .env.example .env
# edit .env: MIKROTIK_URL/USER/PASSWORD, DB_HOST/PORT/NAME/USER/PASSWORD,
# and ADMIN_USER/ADMIN_PASSWORD (leave ADMIN_PASSWORD empty to disable
# auth for local dev only -- see SPEC.md for why that matters here)
docker compose up --build
```

Create the database and a dedicated user on your MariaDB/MySQL server
before starting (see the comment block in `.env.example` for the exact
SQL) — familink runs its migrations automatically on container start, but
it won't create the database or user itself.

Once it's up:
- Admin panel: `http://localhost:8190/devices` (prompts for `ADMIN_USER`/`ADMIN_PASSWORD`)
- Health check: `http://localhost:8190/health` (no auth, safe for uptime monitors)

Within one `SYNC_INTERVAL_S` (60s by default), devices already known to
your router (DHCP leases, hotspot sessions) should start appearing.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install fastapi "uvicorn[standard]" httpx sqlalchemy alembic pymysql jinja2 python-multipart
export MIKROTIK_PASSWORD=... DB_PASSWORD=... DB_HOST=... DB_USER=... DB_NAME=... ADMIN_PASSWORD=...
alembic upgrade head
uvicorn app.main:app --reload --port 8190
```

New model change? `alembic revision --autogenerate -m "describe it"`, review
the generated file, commit it alongside the model change.

## License

MIT — see [LICENSE](LICENSE).
