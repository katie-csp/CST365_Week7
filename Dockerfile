# syntax=docker/dockerfile:1
# HealthQueue API - multi-stage build
#   Stage 1 (builder): installs dependencies into a virtual environment.
#   Stage 2 (runtime): copies only that environment + app.py, runs as non-root.

# The base image is pinned by digest so every build uses the exact same
# bytes. Dependabot (.github/dependabot.yml) proposes digest updates.

# ---------- Stage 1: builder ----------
FROM python:3.11-slim@sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60 AS builder

WORKDIR /build

# Create an isolated virtual environment we can copy to the final stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install runtime dependencies only (no pytest), without caching downloads.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && pip uninstall -y pip setuptools wheel


# ---------- Stage 2: runtime ----------
FROM python:3.11-slim@sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60 AS runtime

# Don't write .pyc files; send logs straight to Docker.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Remove the base image's packaging tools (pip, setuptools, wheel). The app
# doesn't need them at runtime, they carried known CVEs, and removing them
# means nobody can install packages inside the running container.
# Then create an unprivileged user with no login shell.
RUN python -m pip uninstall -y pip setuptools wheel \
 && rm -rf /root/.cache \
 && groupadd --system --gid 10001 appgroup \
 && useradd --system --uid 10001 --gid appgroup --no-create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

# Copy only what the app needs. Files stay owned by root, so the
# app user can read and run them but cannot modify them.
COPY --from=builder /opt/venv /opt/venv
COPY app.py .

USER appuser

EXPOSE 5000

# Docker marks the container "healthy" once /health responds.
# (slim images have no curl, so use Python's standard library.)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=2)" || exit 1

# Gunicorn is a production WSGI server. One worker is used because
# appointments are stored in memory; extra workers would each have
# separate data. Threads still allow concurrent requests.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", \
     "--worker-tmp-dir", "/dev/shm", "--access-logfile", "-", "app:create_app()"]
