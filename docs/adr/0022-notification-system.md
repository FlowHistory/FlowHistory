# ADR 0022: Notification System

## Status
Accepted

## Context

All backup, restore, and retention events are silent — users must check the FlowHistory web UI to know if a backup failed or a restore completed. For a tool that guards against data loss, silent failures defeat the purpose. A notification system sends alerts to external services so users hear about problems without polling the dashboard.

The system must support multiple notification backends (Discord, Slack, Telegram, Pushbullet, Home Assistant) with a clean abstraction so adding new backends is a single-file change. Discord is the first implementation.

### Design Constraints

- Secrets (webhook URLs, bot tokens) must never be stored in the database — env vars only, following the credential pattern from ADR 0021
- Notification failures must never break backup/restore operations
- Per-instance control: each instance can have its own webhook URL, enable/disable flag, and event preferences
- Global fallback: a single webhook URL can serve all instances that don't set their own
- Zero-config default: if no webhook URL is set, the system is inert with no errors

## Decision

### 1. Abstract Base Class

All notification backends implement a common ABC. Each backend owns both formatting and delivery — there is no shared message formatter because each platform has native rich formatting (Discord embeds, Slack blocks, Telegram Markdown).

**`backup/services/notifications/base.py`:**

```python
import abc
from dataclasses import dataclass
from typing import Optional


class NotifyEvent:
    BACKUP_SUCCESS = "backup_success"
    BACKUP_FAILED = "backup_failed"
    RESTORE_SUCCESS = "restore_success"
    RESTORE_FAILED = "restore_failed"
    RETENTION_CLEANUP = "retention_cleanup"

    ALL = {
        BACKUP_SUCCESS, BACKUP_FAILED,
        RESTORE_SUCCESS, RESTORE_FAILED,
        RETENTION_CLEANUP,
    }

    # Default set — actionable events only, not routine noise
    DEFAULT = {BACKUP_FAILED, RESTORE_SUCCESS, RESTORE_FAILED}


@dataclass
class NotificationPayload:
    event: str                       # NotifyEvent constant
    instance_name: str               # config.name
    instance_slug: str               # config.slug
    instance_color: str              # config.color (hex)
    title: str                       # Human-readable one-line summary
    message: str                     # Detail body
    error: Optional[str] = None      # Error message if applicable
    filename: Optional[str] = None   # Backup filename if applicable
    file_size: Optional[int] = None  # Archive size in bytes
    trigger: Optional[str] = None    # "manual", "scheduled", etc.


class NotificationBackend(abc.ABC):

    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable backend name (e.g., 'Discord')."""
        ...

    @abc.abstractmethod
    def is_configured(self, config) -> bool:
        """Return True if this backend has a webhook/token for the given instance.

        Should check per-instance env var first, then global fallback.
        """
        ...

    @abc.abstractmethod
    def send(self, config, payload: NotificationPayload) -> None:
        """Deliver a notification using this instance's config.

        Must not raise — log errors internally.
        """
        ...
```

**Why `config` is passed to `is_configured()` and `send()`:** Each instance can have its own webhook URL. The backend checks the per-instance env var first (`FLOWHISTORY_{PREFIX}_DISCORD_WEBHOOK_URL`), then falls back to the global (`FLOWHISTORY_NOTIFY_DISCORD_WEBHOOK_URL`). This means the same backend may be "configured" for one instance but not another.

**Why no `format_message()` on the ABC:** Discord uses embeds with color and fields. Slack uses blocks. Telegram uses Markdown. A shared formatter produces lowest-common-denominator text that every backend ignores. The `NotificationPayload` dataclass is the shared contract — structured data that each backend formats natively.

### 2. Core Notification Service

**`backup/services/notification_service.py`** — a dispatcher that checks the instance's enable flag and event preferences, then delivers to all backends configured for that instance.

```python
# All known backends — instantiated once, stateless
ALL_BACKENDS = None  # Lazy-initialized list


def notify(config, payload):
    """Send to all backends configured for this instance, if event is enabled."""
    if not config.notify_enabled:
        return

    enabled_events = _get_instance_events(config)
    if payload.event not in enabled_events:
        return

    for backend in _get_backends():
        if backend.is_configured(config):
            try:
                backend.send(config, payload)
            except Exception:
                logger.warning("Backend %s failed", backend.name(), exc_info=True)
```

Key behaviors:
- **Master switch**: `config.notify_enabled` — if `False`, skip everything immediately
- **Per-instance event filtering**: `_get_instance_events(config)` reads `config.notify_events` — empty means defaults, `"none"` disables all, `"all"` enables everything, or comma-separated event names
- **Per-instance backend check**: each backend's `is_configured(config)` checks that instance's env vars
- **Defense-in-depth**: dispatcher catches per-backend exceptions and logs warnings. Never raises.

### 3. Environment Variable Scheme

#### Per-Instance Backend Config (secrets — never in DB)

```bash
# Per-instance Discord webhook (takes priority)
FLOWHISTORY_PROD_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/prod-channel/...
FLOWHISTORY_DEV_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/dev-channel/...
```

Pattern: `FLOWHISTORY_{PREFIX}_DISCORD_WEBHOOK_URL`

#### Global Fallback (used when instance has no override)

```bash
# Global default — applies to any instance without its own webhook
FLOWHISTORY_NOTIFY_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/default/...
```

Pattern: `FLOWHISTORY_NOTIFY_{BACKEND}_{FIELD}`

#### Per-Instance Preferences (existing env var pattern)

```bash
FLOWHISTORY_PROD_NOTIFY=true                                  # enable/disable
FLOWHISTORY_PROD_NOTIFY_EVENTS=backup_failed,restore_failed   # event filter
FLOWHISTORY_DEV_NOTIFY=false                                  # disable for dev
```

#### Lookup Order for Webhook URL

```
1. FLOWHISTORY_{PREFIX}_DISCORD_WEBHOOK_URL  (per-instance)
2. FLOWHISTORY_NOTIFY_DISCORD_WEBHOOK_URL    (global fallback)
3. Not configured → backend skipped for this instance
```

#### Discovery Service Changes

In `backup/services/discovery_service.py`:
- Add `"NOTIFY"`, `"NOTIFY_EVENTS"`, `"DISCORD_WEBHOOK_URL"` to `_KNOWN_FIELDS`
- Add to `bool_map`: `"NOTIFY": "notify_enabled"`
- Add to `env_map`: `"NOTIFY_EVENTS": ("notify_events", str)`
- `DISCORD_WEBHOOK_URL` is a known field for regex matching but is **not** stored in the DB (read at runtime like `_USER`/`_PASS`)

The `NOTIFY` prefix for global env vars is reserved — it won't collide with instance prefixes because `FLOWHISTORY_NOTIFY_DISCORD_WEBHOOK_URL` doesn't match the instance discovery pattern (it requires `_URL` or `_FLOWS_PATH` to register as an instance).

### 4. Model Changes

Add two fields to `NodeRedConfig`:

```python
notify_enabled = models.BooleanField(default=True)
notify_events = models.CharField(
    max_length=200,
    blank=True,
    default="",
    help_text='Comma-separated events, "all", "none", or blank for defaults.',
)
```

Add a runtime credential method (like `get_nodered_credentials()`):

```python
def get_notification_url(self, backend_field):
    """Per-instance first, then global fallback. Never stored in DB."""
    if self.env_prefix:
        url = os.environ.get(f"FLOWHISTORY_{self.env_prefix.upper()}_{backend_field}")
        if url:
            return url.strip()
    return os.environ.get(f"FLOWHISTORY_NOTIFY_{backend_field}", "").strip()
```

Usage: `config.get_notification_url("DISCORD_WEBHOOK_URL")`

### 5. Discord Backend (First Implementation)

**`backup/services/notifications/discord.py`** — sends Discord webhook embeds using `urllib.request` (stdlib, no new dependency).

```python
class DiscordBackend(NotificationBackend):

    def name(self):
        return "Discord"

    def is_configured(self, config):
        return bool(config.get_notification_url("DISCORD_WEBHOOK_URL"))

    def send(self, config, payload):
        webhook_url = config.get_notification_url("DISCORD_WEBHOOK_URL")
        if not webhook_url:
            return

        embed = {
            "title": payload.title,
            "description": payload.message,
            "color": EVENT_COLORS.get(payload.event, 0x6B7280),
            "footer": {"text": f"FlowHistory — {payload.instance_name}"},
        }
        # Add fields for trigger, filename, size, error...
        # POST to webhook_url with {"embeds": [embed]}
```

Color mapping:
| Event | Color |
|-------|-------|
| backup_success | Green (#10B981) |
| backup_failed | Red (#EF4444) |
| restore_success | Blue (#3B82F6) |
| restore_failed | Red (#EF4444) |
| retention_cleanup | Amber (#F59E0B) |

Uses `urllib.request` (stdlib) — no new dependency. 10-second timeout. Logs warnings on failure, never raises.

### 6. Integration Points (Direct Calls)

Direct function calls in the service layer, not Django signals. Rationale:
- Zero existing signal usage in the codebase
- Explicit and auditable — a reader sees the notification call inline
- Only 5 call sites — signals save zero code
- Simpler error isolation with try/except

#### `backup_service.py`

After success (line ~96) and in `_fail()` (line ~188):

```python
def _notify_backup(config, record):
    try:
        from backup.services.notification_service import notify
        from backup.services.notifications.base import NotificationPayload, NotifyEvent

        event = NotifyEvent.BACKUP_SUCCESS if record.status == "success" else NotifyEvent.BACKUP_FAILED
        payload = NotificationPayload(
            event=event,
            instance_name=config.name, instance_slug=config.slug, instance_color=config.color,
            title=f"Backup {'successful' if record.status == 'success' else 'failed'} — {config.name}",
            message=record.filename if record.status == "success" else "Backup attempt failed.",
            error=record.error_message if record.status == "failed" else None,
            filename=record.filename,
            file_size=record.file_size if record.status == "success" else None,
            trigger=record.trigger,
        )
        notify(config, payload)
    except Exception:
        logger.warning("Notification failed after backup", exc_info=True)
```

#### `restore_service.py`

After local success (line ~85), after remote success (line ~178), and in `_fail()` (line ~98). Same pattern.

#### `retention_service.py`

After cleanup with deletions (line ~75, inside `if deleted_by_age or deleted_by_count:`). Only fires when backups were actually deleted.

### 7. Settings UI

Add a **Notifications** section to `backup/templates/backup/settings.html` between "Retention" and "Restore":

```html
<!-- Notifications -->
{% include "backup/components/_card_section_start.html" with title="Notifications" body_class="p-0" extra_class="mb-3" %}
  <table class="w-full text-sm">
    <tbody class="divide-y divide-gray-100 dark:divide-gray-800">
      <tr>
        <th class="th-label w-48">Notifications {% include "backup/components/_tooltip.html" with text="..." %}</th>
        <td>{% if config.notify_enabled %}Enabled{% else %}Disabled{% endif %}</td>
      </tr>
      <tr>
        <th class="th-label">Events {% include "backup/components/_tooltip.html" with text="..." %}</th>
        <td>{{ config.notify_events|default:"Defaults (backup_failed, restore_success, restore_failed)" }}</td>
      </tr>
      <tr>
        <th class="th-label">Discord {% include "backup/components/_tooltip.html" with text="..." %}</th>
        <td>
          {% if discord_instance %}
            <span class="text-green-600 dark:text-green-400">Configured</span>
            <span class="text-xs text-gray-400">(instance)</span>
          {% elif discord_global %}
            <span class="text-green-600 dark:text-green-400">Configured</span>
            <span class="text-xs text-gray-400">(global)</span>
          {% else %}
            <span class="text-gray-400">Not configured</span>
          {% endif %}
        </td>
      </tr>
      <!-- Future backends (Slack, Telegram, etc.) will add rows here -->
    </tbody>
  </table>

  {% if has_any_notification_backend %}
  <div class="border-t border-gray-200 px-4 py-3 dark:border-gray-700">
    <button class="btn-secondary text-xs" onclick="testNotification()">Send Test Notification</button>
  </div>
  {% endif %}
{% include "backup/components/_card_section_end.html" %}
```

The settings UI shows:
- **Enabled/Disabled** — the `notify_enabled` master switch
- **Events** — which events trigger notifications, or "Defaults" if empty
- **Per backend** — whether configured via instance env var, global env var, or not at all
- **Test button** — sends a test notification to verify webhook URLs work

Context variables added to the `instance_settings` view:
- `discord_instance`: `bool` — per-instance Discord webhook is set
- `discord_global`: `bool` — global Discord webhook is set
- `has_any_notification_backend`: `bool` — at least one backend configured for this instance

### 8. Test Notification Endpoint

```
POST /api/instance/<slug>/notifications/test/
```

Sends a test payload to all configured backends for the given instance. Returns JSON:

```json
{"status": "success", "backends": ["Discord"]}
```

or on partial failure:

```json
{"status": "partial", "errors": ["Discord: connection timeout"]}
```

### 9. Adding New Backends Later

To add a backend (e.g., Slack):

1. Create `backup/services/notifications/slack.py` implementing the three ABC methods
2. Add `SlackBackend()` to `_get_backends()` in `notification_service.py`
3. Add `SLACK_WEBHOOK_URL` to `_KNOWN_FIELDS` in `discovery_service.py`
4. Add env var examples to `.env.example`
5. Add a row to the settings template Notifications section

No model changes, no migrations.

### Files Modified

| File | Change |
|------|--------|
| `backup/services/notifications/__init__.py` | New — empty package marker |
| `backup/services/notifications/base.py` | New — ABC, dataclass, event constants |
| `backup/services/notifications/discord.py` | New — Discord webhook backend |
| `backup/services/notification_service.py` | New — dispatcher |
| `backup/models.py` | Add `notify_enabled`, `notify_events` fields + `get_notification_url()` method |
| `backup/services/discovery_service.py` | Add notification fields to `_KNOWN_FIELDS`, `bool_map`, `env_map` |
| `backup/services/backup_service.py` | Add `_notify_backup()` helper + calls |
| `backup/services/restore_service.py` | Add `_notify_restore()` helper + calls |
| `backup/services/retention_service.py` | Add `_notify_retention()` helper + call |
| `backup/views.py` | Add notification context to settings view + test endpoint |
| `backup/urls.py` | Add test notification URL |
| `backup/templates/backup/settings.html` | Add Notifications section with per-backend status + test button |
| `.env.example` | Add notification env var examples |
| `README.md` | Add Notifications section to features, env vars, and API docs |
| `docs/adr/0000-adr-index.md` | Add ADR 0022 entry |

## Alternatives Considered

### Django Signals Instead of Direct Calls
Rejected. Zero existing signal usage, only 5 call sites, signals hide the notification call from code readers, and error isolation is more complex with signal dispatch.

### Shared Message Formatter
Rejected. Each platform (Discord/Slack/Telegram) has native rich formatting that a shared text formatter can't leverage. The `NotificationPayload` dataclass is the shared contract.

### Global-Only Webhook URLs
Rejected. Per-instance webhooks let different instances alert to different channels (e.g., Prod → #prod-alerts, Dev → nowhere). Global fallback covers the common single-webhook case.

### Separate NotificationConfig Model
Rejected. Backend configuration is entirely env-var-driven (webhook URLs are secrets). Per-instance preferences fit in two fields on NodeRedConfig. A separate model adds migration, admin, and sync complexity for no benefit.

## Consequences

**Positive:**
- Users get notified of failures without polling the dashboard
- Per-instance control: different instances can use different webhooks or disable notifications entirely
- Global fallback makes the single-webhook case zero-config (just set one env var)
- Adding new backends is a single-file change with no model/migration work
- Settings UI shows full notification state at a glance — global vs instance, configured vs not
- Follows existing patterns (env vars for secrets, runtime lookups, service layer)

**Negative:**
- Env var changes require container restart (consistent with existing behavior)
- Per-instance webhook URLs add more env vars to manage in large deployments
- Direct calls add 2-3 lines per service function
- `urllib.request` is less ergonomic than `requests`, but avoids tying notification delivery to the HTTP library used for Node-RED API calls

## Todos

- [ ] Create `backup/services/notifications/` package with ABC, dataclass, event constants
- [ ] Implement Discord webhook backend
- [ ] Create core notification service (dispatcher)
- [ ] Add `notify_enabled` and `notify_events` fields to NodeRedConfig + migration
- [ ] Add `get_notification_url()` method to NodeRedConfig
- [ ] Update discovery service with `NOTIFY`, `NOTIFY_EVENTS`, `DISCORD_WEBHOOK_URL`
- [ ] Hook into backup_service.py
- [ ] Hook into restore_service.py
- [ ] Hook into retention_service.py
- [ ] Add Notifications section to settings UI with per-backend status + test button
- [ ] Add test notification endpoint
- [ ] Update .env.example with notification env vars
- [ ] Update README.md with notification docs
- [ ] Write tests
