# ADR 0030: Rename `TIME_ZONE` env var to `TZ`

**Status:** Accepted
**Date:** 2026-05-12
**Author:** Leonardo Merza

## Context

FlowHistory has read its timezone from a custom env var named `TIME_ZONE` since the project was bootstrapped (ADR 0001, ADR 0002). The name was chosen to mirror the Django setting it populates (`settings.TIME_ZONE`).

In the surrounding home-lab docker monorepo (`/media/cubxi/docker/`), every other service consumes `TZ` — the POSIX standard env var that libc, `date`, `cron`, and every mainstream container image already understand. `TZ=America/New_York` is set once in `env/global.env` and inherited across the stack. FlowHistory is the only outlier still using `TIME_ZONE`.

This is a small but real papercut:

- Operators copying conventions from neighboring services trip on the inconsistent name.
- The monorepo's global `TZ` value cannot be reused without aliasing it in `flowhistory.env`.
- Tools that introspect container env (`docker inspect`, dashboards) treat `TZ` as canonical; a custom `TIME_ZONE` shows up as application-specific noise.

The Django setting itself must remain named `TIME_ZONE` — that name is fixed by the framework. Only the *env-var source* changes.

## Decision

Rename the env var FlowHistory reads from `TIME_ZONE` to `TZ`. Update `config/settings.py` to read `os.environ.get("TZ", "America/New_York")`. Default value is unchanged.

## Scope

Files updated in this change:

- `config/settings.py` — read `TZ` instead of `TIME_ZONE`
- `docker-compose.yml` — env mapping renamed
- `.env`, `.env.example` — variable renamed
- `README.md` — env block snippet and env-var table
- `/media/cubxi/docker/env/flowhistory.env` — production env file (outside this repo)

Explicitly **not** changed:

- `backup/management/commands/runapscheduler.py` reads `settings.TIME_ZONE` (the Django setting, not the env). No change needed.
- Older ADRs (0001, 0002, 0003, 0020) reference the old name. They are historical records and remain as-is; this ADR supersedes them on the env-var name.
- The home-lab compose service block at `docker-compose.home.yml` still uses a per-instance `env_file` rather than inheriting from `global.env`. Wiring that up is a follow-up cleanup, not part of this rename.

## Consequences

**Positive**
- Consistent with the rest of the home-lab monorepo and every other Linux container.
- POSIX `TZ` is understood by libc, so `date` inside the container and Python's `tzset()` align with the Django setting automatically.
- Operators get one less custom name to memorize.

**Negative / migration**
- Existing deployments must update their env file before the next deploy. A missed update is non-fatal — `os.environ.get("TZ", "America/New_York")` falls back to the default rather than crashing — but timestamps would silently shift if the operator's actual zone differs from `America/New_York`.

## Verification

1. `docker compose up -d --build`
2. `docker compose exec flowhistory python -c "from django.conf import settings; print(settings.TIME_ZONE)"` prints `America/New_York`.
3. `docker compose exec flowhistory date` prints local time, not UTC.
4. `docker compose exec flowhistory python manage.py test backup -v2` — all tests pass.
5. Visit `http://localhost:9473/`, confirm backup-history timestamps render in local TZ.
