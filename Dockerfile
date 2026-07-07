# Theta Quant engine — portable container.
# Same image runs on the Mac mini (Docker), Fly/Render, or AWS ECS/EC2 later.
# Build:  docker build -t thetaquant-engine .
# Run:    docker compose up   (see docker-compose.yml for the volume mounts)
#
# NOTE: macOS Vision OCR (screenshot import) is darwin-only and is NOT available
# inside this Linux container (pip skips the pyobjc deps via sys_platform markers).
# OCR degrades gracefully; run natively on macOS if you need screenshot OCR, or
# swap in a cloud OCR adapter for a fully-containerised deploy.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TQ_DATA_DIR=/data

WORKDIR /app

# Deps first for layer caching. Linux skips the darwin-only OCR wheels.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# App code only — data & secrets come in as mounted volumes at runtime (never baked in).
COPY lib/ ./lib/
COPY dashboard/ ./dashboard/
COPY scripts/ ./scripts/
COPY analyses/ ./analyses/

EXPOSE 8000

# Writable data (/data) and secrets (/secrets) are volumes; parquet is read-only.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["python", "-m", "uvicorn", "dashboard.server:app", "--host", "0.0.0.0", "--port", "8000"]
