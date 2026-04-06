"""Auto-discover Node-RED instances from FLOWHISTORY_* environment variables."""

import logging
import os
import re

from backup.models import NodeRedConfig

logger = logging.getLogger(__name__)

# Fields recognized after FLOWHISTORY_{PREFIX}_
_KNOWN_FIELDS = {
    "URL", "FLOWS_PATH", "USER", "PASS", "NAME", "COLOR",
    "SCHEDULE", "TIME", "DAY", "MAX_BACKUPS", "MAX_AGE_DAYS",
    "POLL_INTERVAL", "WATCH", "DEBOUNCE", "ALWAYS_BACKUP",
    "BACKUP_CREDENTIALS", "BACKUP_SETTINGS", "RESTART_ON_RESTORE",
    "CONTAINER_NAME",
}

_ENV_PATTERN = re.compile(
    r"^FLOWHISTORY_([A-Z][A-Z0-9_]*?)_(" + "|".join(_KNOWN_FIELDS) + r")$"
)


def _extract_prefixes():
    """Scan os.environ for FLOWHISTORY_*_URL and FLOWHISTORY_*_FLOWS_PATH.

    Returns a dict of {prefix: source_type}.
    """
    prefixes = {}
    for key in os.environ:
        match = _ENV_PATTERN.match(key)
        if not match:
            continue
        prefix, field = match.group(1), match.group(2)
        if field == "URL":
            prefixes[prefix] = "remote"
        elif field == "FLOWS_PATH" and prefix not in prefixes:
            prefixes[prefix] = "local"
    return prefixes


def _bool_env(value):
    return value.lower() in ("true", "1", "yes")


_VALID_FREQUENCIES = {"hourly", "daily", "weekly"}
_VALID_DAYS = set(range(7))  # 0=Monday .. 6=Sunday


def _build_config_kwargs(prefix, source_type):
    """Build kwargs dict for NodeRedConfig creation from env vars."""
    kwargs = {
        "env_prefix": prefix,
        "source_type": source_type,
        "name": os.environ.get(
            f"FLOWHISTORY_{prefix}_NAME",
            prefix.replace("_", " ").title(),
        ),
    }

    if source_type == "remote":
        kwargs["nodered_url"] = os.environ.get(f"FLOWHISTORY_{prefix}_URL", "")
    elif source_type == "local":
        kwargs["flows_path"] = os.environ.get(f"FLOWHISTORY_{prefix}_FLOWS_PATH", "")

    # Optional fields — only set if env var exists
    env_map = {
        "COLOR": ("color", str),
        "SCHEDULE": ("backup_frequency", str),
        "TIME": ("backup_time", str),
        "DAY": ("backup_day", int),
        "MAX_BACKUPS": ("max_backups", int),
        "MAX_AGE_DAYS": ("max_age_days", int),
        "POLL_INTERVAL": ("poll_interval_seconds", int),
        "DEBOUNCE": ("watch_debounce_seconds", int),
        "CONTAINER_NAME": ("nodered_container_name", str),
    }
    for env_suffix, (field_name, converter) in env_map.items():
        value = os.environ.get(f"FLOWHISTORY_{prefix}_{env_suffix}")
        if value is not None:
            kwargs[field_name] = converter(value)

    # Validate enum fields
    freq = kwargs.get("backup_frequency")
    if freq is not None and freq not in _VALID_FREQUENCIES:
        logger.warning("Invalid FLOWHISTORY_%s_SCHEDULE=%r, using default 'daily'", prefix, freq)
        del kwargs["backup_frequency"]

    day = kwargs.get("backup_day")
    if day is not None and day not in _VALID_DAYS:
        logger.warning("Invalid FLOWHISTORY_%s_DAY=%r, using default 0", prefix, day)
        del kwargs["backup_day"]

    bool_map = {
        "WATCH": "watch_enabled",
        "ALWAYS_BACKUP": "always_backup",
        "BACKUP_CREDENTIALS": "backup_credentials",
        "BACKUP_SETTINGS": "backup_settings",
        "RESTART_ON_RESTORE": "restart_on_restore",
    }
    for env_suffix, field_name in bool_map.items():
        value = os.environ.get(f"FLOWHISTORY_{prefix}_{env_suffix}")
        if value is not None:
            kwargs[field_name] = _bool_env(value)

    return kwargs


def discover_instances_from_env(force=False):
    """Scan environment for FLOWHISTORY_* vars and create missing NodeRedConfig rows.

    Args:
        force: If True, re-apply env var values to existing instances
               (except credentials, which are always runtime).

    Returns:
        dict with "created", "skipped", and "updated" lists of prefixes.
    """
    prefixes = _extract_prefixes()
    created, skipped, updated = [], [], []

    for prefix, source_type in prefixes.items():
        existing = NodeRedConfig.objects.filter(env_prefix=prefix).first()

        if existing and not force:
            logger.debug(
                "Instance with env_prefix=%s already exists (pk=%d), skipping",
                prefix, existing.pk,
            )
            skipped.append(prefix)
            continue

        try:
            kwargs = _build_config_kwargs(prefix, source_type)
        except (ValueError, TypeError):
            logger.exception("Invalid env var value for prefix %s, skipping", prefix)
            continue

        if existing and force:
            for field, value in kwargs.items():
                if field != "env_prefix":
                    setattr(existing, field, value)
            existing.save()
            logger.info("Updated instance %s (pk=%d) from env vars", prefix, existing.pk)
            updated.append(prefix)
        else:
            config = NodeRedConfig(**kwargs)
            config.save()
            logger.info(
                "Created instance %s (pk=%d, slug=%s) from env vars",
                prefix, config.pk, config.slug,
            )
            created.append(prefix)

    return {"created": created, "skipped": skipped, "updated": updated}
