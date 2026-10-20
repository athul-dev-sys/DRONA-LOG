FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# libgomp1 is LightGBM's OpenMP runtime; missing it makes `import lightgbm` fail
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY app ./app

# DB + models live here; mount a volume to persist across restarts
RUN mkdir -p /app/backend/data

EXPOSE 8000

# Seed is idempotent (skips if DB exists); models train on first boot
CMD ["sh", "-c", "python -m app.seed && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]
