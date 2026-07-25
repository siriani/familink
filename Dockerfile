FROM python:3.12-slim

WORKDIR /app
# nmap: port scanner (app/portscan.py) -- TCP connect scan only, no
# NET_RAW capability needed
RUN apt-get update && apt-get install -y --no-install-recommends nmap \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
    fastapi uvicorn[standard] httpx sqlalchemy alembic pymysql jinja2 python-multipart aiomqtt babel

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh
# Compiled at build time -- catalogs are static per-image, no runtime
# dependency to justify redoing it on every boot (unlike `alembic upgrade
# head`, which does need to run per-boot against live DB state).
RUN pybabel compile -d app/locales -D familink

EXPOSE 8190
ENTRYPOINT ["./entrypoint.sh"]
