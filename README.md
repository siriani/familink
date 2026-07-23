# familink

A device registry for home networks with a MikroTik router. It watches your
router (read-only), keeps a database of every device that's ever shown up,
and gives you a simple admin UI to see who's online and decide whether each
device is free ("Liberado") or should have to log in through the hotspot
("Hotspot"). New devices always default to free, so IoT/sensor gear never
gets accidentally locked out.

This is the **foundation phase** — see [SPEC.md](SPEC.md) for the full
vision and what's on the roadmap (port scanning, MQTT/Home Assistant
presence publishing, a captive-portal self-registration flow, and pushing
group assignments back to MikroTik as real enforcement). Today, familink
only *reads* from your router — it never changes anything on it.

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
# edit .env: MIKROTIK_URL/USER/PASSWORD and DB_HOST/PORT/NAME/USER/PASSWORD
docker compose up --build
```

Create the database and a dedicated user on your MariaDB/MySQL server
before starting (see the comment block in `.env.example` for the exact
SQL) — familink runs its migrations automatically on container start, but
it won't create the database or user itself.

Once it's up:
- Admin panel: `http://localhost:8190/devices`
- Health check: `http://localhost:8190/health`

Within one `SYNC_INTERVAL_S` (60s by default), devices already known to
your router (DHCP leases, hotspot sessions) should start appearing.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install fastapi "uvicorn[standard]" httpx sqlalchemy alembic pymysql jinja2 python-multipart
export MIKROTIK_PASSWORD=... DB_PASSWORD=... DB_HOST=... DB_USER=... DB_NAME=...
alembic upgrade head
uvicorn app.main:app --reload --port 8190
```

New model change? `alembic revision --autogenerate -m "describe it"`, review
the generated file, commit it alongside the model change.

## License

MIT — see [LICENSE](LICENSE).
