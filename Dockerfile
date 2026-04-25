# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  Shelfie — multi-stage Dockerfile                                         ║
# ║                                                                              ║
# ║  Stage 1  builder  — installs the wheel and all heavy C-extension deps      ║
# ║  Stage 2  runtime  — copies only the installed site-packages + entrypoint   ║
# ║                                                                              ║
# ║  Persistent data lives at /data (mount a named volume or bind-mount there). ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ── Stage 1: builder ───────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

# Build-time dependencies only needed for compiling C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libxml2-dev \
        libxslt-dev \
        libjpeg-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Use a venv so the runtime stage can copy a clean, self-contained tree
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip + wheel first for faster subsequent installs
RUN pip install --upgrade pip wheel hatchling

# Copy project metadata and source — let pip resolve deps from pyproject.toml
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src/ src/

# Install the package (and all its dependencies) into the venv
RUN pip install --no-cache-dir ".[dev]" || pip install --no-cache-dir .


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="Shelfie"
LABEL org.opencontainers.image.description="Self-hosted local library manager"
LABEL org.opencontainers.image.version="0.2.0"

# Runtime system libraries needed by PyMuPDF and Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
        libjpeg62-turbo \
        zlib1g \
        libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security
RUN useradd --create-home --shell /bin/bash shelfie

# Pre-create the data directories and give ownership to shelfie
# This runs as root (before USER directive) so the chown succeeds
# even when /data is a mounted volume
RUN mkdir -p /data/covers && chown -R shelfie:shelfie /data

USER shelfie
WORKDIR /home/shelfie

# Copy the fully-built venv from the builder stage
COPY --from=builder --chown=shelfie:shelfie /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# ── Data volume ───────────────────────────────────────────────────────────────
# /data is the single persistence mount point.
#
#   /data/library.db          — SQLite database
#   /data/covers/             — generated + custom cover images
#   /data/library_path.txt    — watched folder config
#   /books                    — bind-mount your book collection here
#
VOLUME ["/data"]

# Tell shelfie to store everything under /data
ENV SHELFIE_DATA_DIR=/data
ENV SHELFIE_DB_URL=sqlite:////data/library.db
ENV SHELFIE_COVERS_DIR=/data/covers
ENV SHELFIE_CONFIG=/data/library_path.txt

# The watched library lives at /books inside the container.
# Override with LIBRARY_PATH env var or set it via the UI at runtime.
ENV LIBRARY_PATH=/books

# Web server
EXPOSE 8000

# Healthcheck — lightweight ping every 30 s
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/stats')" \
    || exit 1

# Start the server bound to all interfaces so Docker port-mapping works
CMD ["shelfie", "--host", "0.0.0.0", "--port", "8000"]
