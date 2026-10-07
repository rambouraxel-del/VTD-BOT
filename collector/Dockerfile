# Collector VTD : Python standard uniquement, image légère.
FROM python:3.13-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STATE_FILE=/data/state.json \
    SEARCHES_FILE=/app/searches.json

WORKDIR /app
COPY collector.py vinted.py searches.json ./
COPY fixtures/ ./fixtures/

RUN adduser -D -u 1000 app && mkdir -p /data && chown app:app /data
USER app

# « healthy » si un passage a eu lieu dans les 2 dernières heures.
HEALTHCHECK --interval=5m --timeout=5s --start-period=10m \
    CMD python -c "import os,sys,time; p='/data/heartbeat'; sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p) < 7200 else 1)"

ENTRYPOINT ["python", "collector.py"]
