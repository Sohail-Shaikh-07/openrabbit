# syntax=docker/dockerfile:1.7

# ---------- Build stage ----------
FROM python:3.12-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=2.4.1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN pip install "poetry==${POETRY_VERSION}"

WORKDIR /build

COPY pyproject.toml poetry.lock README.md ./
COPY src ./src

RUN poetry build --format wheel \
 && ls -la dist/

# ---------- Runtime stage ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OPENRABBIT_HOME=/workspace

RUN useradd --create-home --uid 1000 openrabbit \
 && mkdir -p /workspace \
 && chown openrabbit:openrabbit /workspace

COPY --from=build /build/dist/*.whl /tmp/

RUN pip install /tmp/openrabbit-*.whl \
 && rm /tmp/openrabbit-*.whl

USER openrabbit
WORKDIR /workspace

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD openrabbit --version || exit 1

ENTRYPOINT ["openrabbit"]
CMD ["--help"]
