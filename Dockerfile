# QuickBite AI + Loyalty — production image (DOCKER ticket)
# python:3.12-slim + non-root user per Doc 5 security requirement.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements/ requirements/
RUN pip install --no-cache-dir -r requirements/prod.txt

COPY alembic.ini ./
COPY app/ app/
COPY scripts/ scripts/
COPY static/ static/

RUN groupadd --system quickbite && useradd --system --gid quickbite quickbite \
    && chown -R quickbite:quickbite /app
USER quickbite

EXPOSE 8000

CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "4", "--bind", "0.0.0.0:8000"]
