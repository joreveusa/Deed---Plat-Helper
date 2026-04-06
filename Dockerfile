# ── Deed & Plat Helper — Production Dockerfile ────────────────────────────────
# Builds a Linux container with Tesseract OCR + Python deps.
# Used by Render.com for cloud deployment.
# ──────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# System deps: Tesseract OCR + its English data pack, plus PDF/image libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (server-only — no pywebview/pystray)
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

# Copy source code
COPY . .

# Create persistent data directory (Render Disk mounts here)
RUN mkdir -p /data

# Expose port
EXPOSE 10000

# Production server: gunicorn with 2 workers
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--workers", "2", "--timeout", "120", "--access-logfile", "-"]
