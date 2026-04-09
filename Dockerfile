FROM python:3.13-slim AS builder

WORKDIR /app
COPY pyproject.toml uv.lock ./

RUN pip install uv && \
    uv sync --frozen --no-dev

FROM python:3.13-slim AS tailwind

ARG TARGETARCH
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*
RUN ARCH=$([ "$TARGETARCH" = "arm64" ] && echo "arm64" || echo "x64") && \
    curl -sL "https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-${ARCH}" \
    -o /usr/local/bin/tailwindcss && chmod +x /usr/local/bin/tailwindcss
COPY tailwind.config.js ./
COPY backup/templates/ backup/templates/
COPY backup/forms.py backup/forms.py
COPY backup/static/backup/css/input.css backup/static/backup/css/input.css
RUN tailwindcss -i backup/static/backup/css/input.css \
                -o backup/static/backup/css/tailwind.css --minify

FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY . .

COPY --from=builder /app/.venv /app/.venv
COPY --from=tailwind /app/backup/static/backup/css/tailwind.css backup/static/backup/css/tailwind.css
ENV PATH="/app/.venv/bin:$PATH"

RUN python manage.py collectstatic --noinput

ARG GIT_COMMIT_SHORT=dev
ARG BUILD_DATE=""
ARG BUILD_REPO=""
ENV GIT_COMMIT_SHORT=${GIT_COMMIT_SHORT}
ENV BUILD_DATE=${BUILD_DATE}
ENV BUILD_REPO=${BUILD_REPO}

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/health/ || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
