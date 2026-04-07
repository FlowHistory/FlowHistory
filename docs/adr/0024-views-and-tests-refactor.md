# ADR 0024: Split views.py and tests.py into Packages

## Status
Implemented

## Context

Two files have grown large enough that navigating, testing, and reviewing changes is harder than it needs to be:

| File | Lines | Contents |
|------|-------|----------|
| `backup/views.py` | 764 | 20 views/endpoints spanning pages, API, auth, and diff |
| `backup/tests.py` | 2,531 | 39 test classes covering every service, model, view, and notification backend |

Other files were evaluated and don't need splitting:

| File | Lines | Verdict |
|------|-------|---------|
| `backup/models.py` | 210 | 3 focused models — clean and cohesive |
| `backup/urls.py` | 36 | Flat list, fine as-is |
| `backup/services/*.py` | 70–298 each | Already split by domain |

### Pain Points

- **`tests.py`**: Finding a specific test class requires scrolling through 2,500 lines. Running a single test domain (e.g., notification tests) means specifying a long dotted path. Merge conflicts are frequent when multiple features touch tests.
- **`views.py`**: Page views, JSON API endpoints, diff logic, and auth handlers are interleaved. The `diff_view` function alone is 113 lines with template-prep logic that obscures the actual view contract. API endpoints share repeated JSON-parse/validate boilerplate.

### Design Constraints

- No behavior changes — pure file reorganization
- `urls.py` import paths must keep working (use `__init__.py` re-exports)
- `python manage.py test backup` must keep working unchanged
- No new dependencies

## Decision

### 1. Convert `views.py` → `views/` Package

Split by responsibility into 4 modules:

```
backup/views/
├── __init__.py      # Re-exports all views so urls.py imports are unchanged
├── pages.py         # Server-rendered page views
├── api.py           # JSON API endpoints
├── backups.py       # Backup detail, download, delete, diff
└── auth.py          # Login, logout, health check, error handlers
```

#### `pages.py` (~165 lines)
Instance management and settings — views that render full HTML pages.

| View | Current lines |
|------|---------------|
| `dashboard()` | 35–65 |
| `instance_add()` | 74–80 |
| `instance_dashboard()` | 89–102 |
| `instance_settings()` | 111–163 |
| `instance_delete()` | 171–209 |

Also includes the `_get_config()` helper since every module needs it — or it moves to a `_helpers.py` if a circular import arises.

#### `backups.py` (~185 lines)
Backup-specific views that render HTML or serve files, plus the diff viewer.

| View | Current lines |
|------|---------------|
| `backup_detail()` | 218–250 |
| `backup_download()` | 254–264 |
| `backup_delete()` | 268–280 |
| `_classify_diff_lines()` | 288–301 |
| `diff_view()` | 305–417 |

#### `api.py` (~310 lines)
All `api_*` JSON endpoints. These share a common pattern (get config, get backup, parse JSON body, return JsonResponse) making them a natural group.

| View | Current lines |
|------|---------------|
| `api_create_backup()` | 426–476 |
| `api_restore_backup()` | 480–514 |
| `api_set_label()` | 518–551 |
| `api_set_notes()` | 555–583 |
| `api_toggle_pin()` | 587–600 |
| `api_bulk_action()` | 604–645 |
| `api_test_notification()` | 649–685 |
| `api_test_connection()` | 689–728 |

#### `auth.py` (~35 lines)
Authentication and system endpoints.

| View | Current lines |
|------|---------------|
| `health_check()` | 738 |
| `login_view()` | 741–748 |
| `logout_view()` | 752–754 |
| `custom_404()` | 757–759 |
| `custom_500()` | 762–764 |

#### `__init__.py`
Re-exports every public view so `urls.py` stays unchanged:

```python
from .pages import (
    dashboard, instance_add, instance_dashboard,
    instance_settings, instance_delete,
)
from .backups import backup_detail, backup_download, backup_delete, diff_view
from .api import (
    api_create_backup, api_restore_backup, api_set_label,
    api_set_notes, api_toggle_pin, api_bulk_action,
    api_test_notification, api_test_connection,
)
from .auth import health_check, login_view, logout_view, custom_404, custom_500
```

With this `__init__.py`, `urls.py` keeps its `from . import views` / `views.dashboard` pattern with zero changes.

### 2. Convert `tests.py` → `tests/` Package

Split by domain into 13 modules plus shared fixtures:

```
backup/tests/
├── __init__.py
├── helpers.py                    # TempBackupDirMixin, SAMPLE_FLOWS, shared utilities
├── test_flow_parser.py           # FlowParserParseFlowsTest, FlowParserFileTest
├── test_diff.py                  # DiffTabSummariesTest, DiffServiceArchiveTest, DiffViewTest
├── test_backup.py                # BackupServiceTest, ApiCreateBackupTest
├── test_restore.py               # RestoreServiceTest, ApiRestoreBackupTest, RemoteRestoreTest
├── test_retention.py             # RetentionServiceTest
├── test_api.py                   # ApiSetLabelTest, ApiSetNotesTest, ApiTogglePinTest, BackupDeleteTest, BulkActionTest
├── test_models.py                # NodeRedConfigModelTest
├── test_views.py                 # AggregateDashboardTest, InstanceIsolationTest, InstanceDeleteTest
├── test_watcher.py               # WatcherHandlerTest, SchedulerBuildTriggerTest
├── test_discovery.py             # DiscoveryServiceTest
├── test_remote.py                # RemotePollerTest
├── test_docker.py                # DockerServiceTest
└── test_notifications.py         # All 12 notification test classes
```

#### Test class → module mapping

| Module | Test Classes | ~Lines |
|--------|-------------|--------|
| `helpers.py` | `TempBackupDirMixin`, `SAMPLE_FLOWS` | 35 |
| `test_flow_parser.py` | `FlowParserParseFlowsTest`, `FlowParserFileTest` | 100 |
| `test_diff.py` | `DiffTabSummariesTest`, `DiffServiceArchiveTest`, `DiffViewTest` | 295 |
| `test_backup.py` | `BackupServiceTest`, `ApiCreateBackupTest` | 170 |
| `test_restore.py` | `RestoreServiceTest`, `ApiRestoreBackupTest`, `RemoteRestoreTest` | 225 |
| `test_retention.py` | `RetentionServiceTest` | 115 |
| `test_api.py` | `ApiSetLabelTest`, `ApiSetNotesTest`, `ApiTogglePinTest`, `BackupDeleteTest`, `BulkActionTest` | 330 |
| `test_models.py` | `NodeRedConfigModelTest` | 75 |
| `test_views.py` | `AggregateDashboardTest`, `InstanceIsolationTest`, `InstanceDeleteTest` | 80 |
| `test_watcher.py` | `WatcherHandlerTest`, `SchedulerBuildTriggerTest` | 105 |
| `test_discovery.py` | `DiscoveryServiceTest` | 95 |
| `test_remote.py` | `RemotePollerTest` | 75 |
| `test_docker.py` | `DockerServiceTest` | 45 |
| `test_notifications.py` | 12 classes (events, dispatcher, 5 backends, 3 integration, API test) | 700 |

#### `helpers.py`

Shared test infrastructure used across multiple modules:

```python
# TempBackupDirMixin — redirects BACKUP_DIR to a temp directory
# SAMPLE_FLOWS — canonical test fixture for flow parsing and backup tests
```

Each test module imports what it needs:
```python
from backup.tests.helpers import TempBackupDirMixin, SAMPLE_FLOWS
```

### 3. What Does NOT Change

- **`models.py`** (210 lines) — 3 models with clear boundaries. Not worth the package overhead.
- **`urls.py`** (36 lines) — unchanged thanks to `views/__init__.py` re-exports.
- **`services/`** — already organized by domain (70–298 lines each). The largest files (`remote_service.py` at 298, `diff_service.py` at 276) are within reasonable bounds.
- **`admin.py`** (49 lines) — fine.
- **`forms.py`** (2 lines) — effectively deprecated (env vars only).

### Files Modified

| File | Change |
|------|--------|
| `backup/views.py` | Deleted — replaced by `backup/views/` package |
| `backup/views/__init__.py` | New — re-exports all views |
| `backup/views/pages.py` | New — page views |
| `backup/views/backups.py` | New — backup detail/download/delete/diff |
| `backup/views/api.py` | New — JSON API endpoints |
| `backup/views/auth.py` | New — login/logout/health/errors |
| `backup/tests.py` | Deleted — replaced by `backup/tests/` package |
| `backup/tests/__init__.py` | New — empty package marker |
| `backup/tests/helpers.py` | New — TempBackupDirMixin, SAMPLE_FLOWS |
| `backup/tests/test_flow_parser.py` | New — flow parser tests |
| `backup/tests/test_diff.py` | New — diff service + diff view tests |
| `backup/tests/test_backup.py` | New — backup service tests |
| `backup/tests/test_restore.py` | New — restore service tests |
| `backup/tests/test_retention.py` | New — retention service tests |
| `backup/tests/test_api.py` | New — backup API endpoint tests |
| `backup/tests/test_models.py` | New — model tests |
| `backup/tests/test_views.py` | New — page view tests |
| `backup/tests/test_watcher.py` | New — watcher + scheduler tests |
| `backup/tests/test_discovery.py` | New — discovery service tests |
| `backup/tests/test_remote.py` | New — remote poller tests |
| `backup/tests/test_docker.py` | New — docker service tests |
| `backup/tests/test_notifications.py` | New — all notification tests |
| `docs/adr/0000-adr-index.md` | Add ADR 0024 entry |

## Alternatives Considered

### Split models.py into a Package
Rejected. At 210 lines with 3 models (`NodeRedConfig`, `BackupRecord`, `RestoreRecord`), the file is cohesive and easy to navigate. Splitting 3 models into separate files adds import complexity without meaningful benefit.

### Split Services into Sub-packages
Deferred. The largest service files (250–298 lines) are within reasonable bounds and each handles a single domain. If a service grows past ~400 lines in a future feature, it can be split then.

### Keep tests.py as One File, Use Test Tags
Rejected. Django test tags help run subsets but don't solve the navigation and merge-conflict problems. The file is 2,531 lines — splitting is the straightforward fix.

### Move diff_view Template Prep Logic to diff_service
Considered but deferred. The `diff_view` function (113 lines) has significant template-prep logic (tab overview, summary stats, diff line classification) that could move to the service layer. This is a behavior change better handled in a separate ADR.

## Consequences

**Positive:**
- Test files are 45–700 lines each instead of one 2,531-line monolith
- Running domain-specific tests is simpler: `python manage.py test backup.tests.test_notifications`
- View modules have clear responsibilities: pages vs API vs auth
- Merge conflicts reduced — features typically touch one view module and one test module
- No behavior changes, no migration, no URL changes

**Negative:**
- More files to navigate (mitigated by clear naming and IDE file search)
- `views/__init__.py` must be kept in sync when views are added/removed
- One-time effort to review the split and verify all imports resolve

## Todos

- [x] Create `backup/views/` package with `__init__.py`, `pages.py`, `backups.py`, `api.py`, `auth.py`
- [x] Verify `urls.py` still resolves all views via the `__init__.py` re-exports
- [x] Create `backup/tests/` package with `__init__.py` and `helpers.py`
- [x] Split test classes into domain modules (13 files)
- [x] Run full test suite to confirm no breakage: `docker exec flowhistory python manage.py test backup -v2`
- [x] Delete old `backup/views.py` and `backup/tests.py`
- [x] Update ADR index
