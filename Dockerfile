FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    PORT=8000

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

RUN useradd --uid 10001 --create-home appuser \
 && mkdir -p /data /config \
 && chown 10001:10001 /data

USER 10001
EXPOSE 8000
VOLUME ["/data"]

# 2 Worker × 8 Threads reichen locker für mehrere Klassen gleichzeitig
CMD ["sh", "-c", "exec gunicorn --workers 2 --threads 8 --bind 0.0.0.0:${PORT} --access-logfile - app.main:app"]
