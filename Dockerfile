FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir \
    fastapi uvicorn[standard] httpx sqlalchemy alembic pymysql jinja2 python-multipart

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

EXPOSE 8190
ENTRYPOINT ["./entrypoint.sh"]
