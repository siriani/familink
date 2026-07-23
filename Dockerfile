FROM python:3.12-slim

WORKDIR /app
# nmap: port scanner (app/portscan.py) -- TCP connect scan only, no
# NET_RAW capability needed
RUN apt-get update && apt-get install -y --no-install-recommends nmap \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
    fastapi uvicorn[standard] httpx sqlalchemy alembic pymysql jinja2 python-multipart

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

EXPOSE 8190
ENTRYPOINT ["./entrypoint.sh"]
