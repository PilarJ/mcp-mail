FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

# Run as an unprivileged user. /data is created and chowned before the VOLUME
# declaration so a fresh named volume inherits the ownership.
RUN useradd --system --create-home --uid 10001 --user-group app \
    && mkdir -p /data \
    && chown app:app /data

# Audit log of sent mail; mount a volume so it survives container replacement.
VOLUME ["/data"]
ENV DATA_DIR=/data

# FastMCP keeps OAuth client registrations and the tokens it issued under its
# home directory, which defaults to somewhere inside the container. Left there,
# every rebuild silently signs every connector out. Point it at the volume.
ENV FASTMCP_HOME=/data/fastmcp

EXPOSE 8000

USER app

CMD ["mail-mcp"]
