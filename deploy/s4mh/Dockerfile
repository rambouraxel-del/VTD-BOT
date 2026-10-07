# Image de s4mh/vinted-bot, construite depuis son dépôt GitHub (contexte
# « s4mh_src », version figée par S4MH_REF dans .env).
# Identique au Dockerfile de s4mh, avec un correctif : `greenlet`, requis par
# SQLAlchemy async mais absent de requirements.txt (sinon crash au démarrage).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Paris

WORKDIR /app

COPY --from=s4mh_src requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt greenlet

COPY --from=s4mh_src app/ ./app/
COPY --from=s4mh_src simulator/ ./simulator/
COPY --from=s4mh_src main.py pyproject.toml config.example.yaml ./

RUN mkdir -p /app/data /app/logs /app/config \
    && useradd --create-home --uid 1000 vinted \
    && chown -R vinted:vinted /app
USER vinted

EXPOSE 3000

# s4mh est « healthy » si son dashboard interne répond.
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:3000/api/status', timeout=4).status == 200 else 1)"

ENTRYPOINT ["python", "main.py"]
