FROM python:3.12-slim

WORKDIR /app

# A Unicode font for the PDF attached to scheduled reports
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# pywebview is only needed for the Windows desktop window
COPY requirements.txt .
RUN grep -v pywebview requirements.txt > requirements.docker.txt \
    && pip install --no-cache-dir -r requirements.docker.txt

COPY app.py db.py paths.py reporting.py security.py ./
COPY templates ./templates
COPY static ./static

# Databases and secret.key live in /data — mount a volume there to keep them
ENV ZRS_HOST=0.0.0.0 \
    ZRS_PORT=5050 \
    ZRS_DATA_DIR=/data \
    PYTHONUNBUFFERED=1
RUN useradd --create-home zrs && mkdir /data && chown zrs /data
USER zrs
VOLUME /data
EXPOSE 5050

CMD ["python", "app.py"]
