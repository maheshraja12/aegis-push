FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AEGIS_ENV=production \
    AEGIS_DB_PATH=/var/data/aegis_v3_audit.db

WORKDIR /app
RUN useradd --create-home --uid 10001 appuser

COPY requirements_v3.txt /app/requirements_v3.txt
RUN python -m pip install --upgrade pip && pip install -r requirements_v3.txt

COPY --chown=appuser:appuser . /app
RUN mkdir -p /var/data && chown -R appuser:appuser /var/data /app
USER appuser

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8001')+'/health', timeout=3)" || exit 1

# The production dashboard is read-only by default. Pipeline execution stays disabled.
CMD ["sh", "-c", "exec python run_aegis_v3.py --mode dashboard --host 0.0.0.0 --port ${PORT:-8001}"]
