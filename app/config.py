"""Env-based config. Required values use os.environ[...] (fail fast, no
silent default); optional values use os.environ.get(...) with a sane
default. Mirrors the convention already used in this ecosystem's
cam-gateway service.
"""
import os

# MikroTik REST API
MIKROTIK_URL = os.environ.get("MIKROTIK_URL", "http://192.168.1.1")
MIKROTIK_USER = os.environ.get("MIKROTIK_USER", "admin")
MIKROTIK_PASSWORD = os.environ["MIKROTIK_PASSWORD"]

# Database (PyMySQL driver — see app/db.py for why sync, not async).
# familink does not bundle a database — bring your own MariaDB/MySQL and
# point these at it (see .env.example for the CREATE DATABASE/USER snippet).
DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = os.environ.get("DB_PORT", "3306")
DB_NAME = os.environ.get("DB_NAME", "familink")
DB_USER = os.environ.get("DB_USER", "familink")
DB_PASSWORD = os.environ["DB_PASSWORD"]

DATABASE_URL = (
    f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# Discovery sync loop
SYNC_INTERVAL_S = float(os.environ.get("SYNC_INTERVAL_S", "60"))

# Admin panel auth (HTTP Basic). Empty ADMIN_PASSWORD = no auth enforced —
# fine for local dev, NOT recommended for anything reachable beyond your
# own machine. Same "empty = disabled, not a hard requirement" shape as
# CAM_GATEWAY_TOKEN elsewhere in this ecosystem, so app/auth.py can warn
# loudly at startup instead of just silently being open.
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
