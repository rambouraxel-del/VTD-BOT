# Pont API VTD : Python standard uniquement, image légère (~60 Mo).
FROM python:3.13-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8787 \
    VTD_DB=/data/vtd.db

WORKDIR /app
COPY api.py sources.py store.py backup.py mock_listings.json ./

# Utilisateur non privilégié (même uid que s4mh) ; /data = volume persistant.
RUN adduser -D -u 1000 app && mkdir -p /data && chown app:app /data
USER app

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=4).status == 200 else 1)"

CMD ["python", "api.py"]
