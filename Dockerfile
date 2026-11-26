FROM python:3.15-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

# Run as an unprivileged user. /data must exist and be owned by it before the
# VOLUME line so fresh named volumes inherit that ownership. The group gets an
# explicit GID: a --system user would otherwise get one from the system range.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/fastmcp \
    && chown -R app:app /data

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
