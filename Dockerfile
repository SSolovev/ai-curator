# syntax=docker/dockerfile:1.7
FROM python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS builder
WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml README.md requirements.prod.lock ./
COPY src ./src
RUN python -m pip wheel --wheel-dir=/wheels --no-deps . && python -m pip wheel --wheel-dir=/wheels -r requirements.prod.lock

FROM python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS runtime
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid ${APP_GID} curator && useradd --uid ${APP_UID} --gid ${APP_GID} --no-create-home --shell /usr/sbin/nologin curator
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels ai-curator==0.1.0 && rm -rf /wheels
COPY --chown=${APP_UID}:${APP_GID} schemas ./schemas
COPY --chown=${APP_UID}:${APP_GID} prompts ./prompts
COPY --chown=${APP_UID}:${APP_GID} src ./src
RUN mkdir /data && chown ${APP_UID}:${APP_GID} /data
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CURATOR_ROOT=/app CURATOR_HOST=0.0.0.0 CURATOR_DATA_DIR=/data
USER ${APP_UID}:${APP_GID}
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]
ENTRYPOINT ["python", "-m", "ai_curator.main"]
