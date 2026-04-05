# ADR 0021: Instance Configuration and Credential Storage

## Status
Accepted

## Context

ADR 0013 introduces multi-instance support, where FlowHistory manages backups for multiple Node-RED instances. Remote instances require credentials (username/password) to access the Node-RED Admin API (`GET /flows`). We need to decide how instance configuration and credentials are stored.

Key constraints:
- Home-lab tool, not enterprise — security model should be practical, not paranoid
- Docker-based deployment with `docker-compose.yml` and `.env` file
- Currently all config lives in the database (SQLite), managed via the settings UI
- Credentials are Node-RED admin passwords (same trust boundary as the Node-RED instance itself)
- Need to support 1-10 instances realistically
- Must be fully deployable via automation (Ansible, Terraform, etc.) without touching the UI

## Options Evaluated

### Option A: Everything in Database (UI-only)

Store all instance config — including credentials — in `NodeRedConfig` model fields, managed entirely through the settings page.

```
NodeRedConfig:
  source_type: "local" | "remote"
  nodered_url: CharField (for remote)
  nodered_username: CharField (for remote)
  nodered_password: CharField (for remote)  # plaintext in SQLite
```

**Pros:**
- Simplest implementation — no new config mechanisms
- Single source of truth — everything in the database
- Users manage everything through the UI they already know
- Adding/removing instances doesn't require editing files or restarting

**Cons:**
- Credentials stored in plaintext in SQLite
- Database file in a Docker volume could be copied/leaked
- No way to inject credentials from a secrets manager or CI/CD pipeline
- Can't version-control the configuration (DB is binary)
- Not automation-friendly — requires UI or API interaction to configure

**Security assessment:** SQLite lives in a Docker volume on the host filesystem. Anyone with access to the volume can read it. However, this is the same trust boundary as the `.env` file, `docker-compose.yml`, and the Node-RED instance itself. For a home-lab tool, this is acceptable.

---

### Option B: YAML Configuration File

All instance config in a YAML file mounted into the container.

```yaml
# config/instances.yml
instances:
  - name: Production
    source_type: remote
    url: http://192.168.1.50:1880
    username: admin
    password: secretpass1
    backup_frequency: daily
    backup_time: "03:00"

  - name: Development
    source_type: local
    flows_path: /nodered-data/flows.json
    backup_frequency: hourly
```

```yaml
# docker-compose.yml
volumes:
  - ./instances.yml:/app/config/instances.yml
```

**Pros:**
- Version-controllable (minus credentials)
- Familiar pattern for Docker users
- Easy to see all instances at a glance
- Can be templated by automation tools (Ansible, Terraform)

**Cons:**
- Credentials in plaintext in a file on disk — same exposure as SQLite, but now also easily committed to git by accident
- Split-brain: config in file + runtime state in DB — which is authoritative?
- Changes require container restart (or a reload mechanism)
- Need to sync YAML ↔ DB on startup (what if they conflict?)
- Can't edit config from the UI without also writing back to the YAML
- More code to maintain (YAML parser, validation, sync logic)

**Security assessment:** Same as Option A — plaintext on disk. Arguably worse because a YAML file is more likely to be accidentally committed to git than a SQLite database.

---

### Option C: YAML with Environment Variable Interpolation

YAML file with `${VAR}` placeholders that get resolved from environment variables.

```yaml
# config/instances.yml
instances:
  - name: Production
    source_type: remote
    url: http://192.168.1.50:1880
    username: ${FLOWHISTORY_PROD_USER}
    password: ${FLOWHISTORY_PROD_PASS}

  - name: Development
    source_type: remote
    url: http://192.168.1.51:1880
    username: ${FLOWHISTORY_DEV_USER}
    password: ${FLOWHISTORY_DEV_PASS}
```

```bash
# .env
FLOWHISTORY_PROD_USER=admin
FLOWHISTORY_PROD_PASS=secretpass1
FLOWHISTORY_DEV_USER=admin
FLOWHISTORY_DEV_PASS=secretpass2
```

**Pros:**
- Credentials stay in `.env` (not in the YAML that might be committed)
- YAML structure is version-controllable
- Pattern used by tools like Docker Compose, Grafana, Home Assistant
- Compatible with Docker secrets / external secret managers

**Cons:**
- All the split-brain problems of Option B, plus:
- More complexity — need a YAML template engine
- Env var naming gets unwieldy with many instances
- Two files to manage instead of one
- Still plaintext in `.env` on disk
- Harder to troubleshoot (is the var not set? misspelled? wrong file?)
- Can't add instances from the UI without updating both YAML and env vars

**Security assessment:** Marginally better — credentials are in `.env` which is conventionally gitignored, while the YAML structure can be committed. But `.env` is still plaintext on disk.

---

### Option D: Environment Variables with Auto-Discovery + UI Override

All instance config can be defined via env vars using a `FLOWHISTORY_{PREFIX}_*` convention. FlowHistory scans for these on startup and auto-creates `NodeRedConfig` rows. The settings UI can override non-secret values after initial creation.

```bash
# .env — fully automation-deployable
# Required (triggers auto-registration)
FLOWHISTORY_PROD_URL=http://192.168.1.50:1880

# Auth (optional — no auth if omitted)
FLOWHISTORY_PROD_USER=admin
FLOWHISTORY_PROD_PASS=secretpass1

# All optional — fall back to model defaults
FLOWHISTORY_PROD_NAME=Production
FLOWHISTORY_PROD_COLOR=#3B82F6
FLOWHISTORY_PROD_SCHEDULE=daily
FLOWHISTORY_PROD_TIME=03:00
FLOWHISTORY_PROD_DAY=0
FLOWHISTORY_PROD_MAX_BACKUPS=20
FLOWHISTORY_PROD_MAX_AGE_DAYS=30
FLOWHISTORY_PROD_POLL_INTERVAL=60
FLOWHISTORY_PROD_WATCH=true
FLOWHISTORY_PROD_ALWAYS_BACKUP=false
FLOWHISTORY_PROD_BACKUP_CREDENTIALS=false
FLOWHISTORY_PROD_BACKUP_SETTINGS=false
FLOWHISTORY_PROD_RESTART_ON_RESTORE=false
FLOWHISTORY_PROD_CONTAINER_NAME=nodered

# Local instance — no URL means local source type
FLOWHISTORY_LOCAL_FLOWS_PATH=/nodered-data/flows.json
```

**Discovery logic:**
- Scan env for `FLOWHISTORY_*_URL` → remote instance
- Scan env for `FLOWHISTORY_*_FLOWS_PATH` (without matching `_URL`) → local instance
- Having both `_URL` and `_FLOWS_PATH` for the same prefix → warning, prefer remote
- Auto-create `NodeRedConfig` row on first startup if prefix not yet in DB

**Seeding behavior:**
- Env vars seed the config on **first creation only**
- After creation, UI edits take precedence — env vars don't overwrite user changes on restart
- To re-apply env var values to existing instances: `python manage.py discover_instances --force` (does not affect credentials)
- Credentials (`_USER`, `_PASS`) are always read from env at runtime (never stored in DB)

**Pros:**
- Fully automation-deployable — define `.env`, deploy, done
- No split-brain — DB is authoritative for config, env seeds initial values and provides credentials at runtime
- Standard Docker `.env` pattern
- UI still works for manual tweaks after initial deployment
- Compatible with Docker secrets / external secret injection
- Credentials never stored in database
- Single file to manage (`.env`)

**Cons:**
- Many possible env vars per instance (though all but one are optional)
- Env var names must follow strict convention
- Container restart needed for new instances (to pick up new env vars)
- First-creation-only seeding can be confusing ("I changed the env var but nothing happened") — needs clear documentation

**Security assessment:** Credentials in `.env` (gitignored, standard Docker practice), never in DB. Non-secret config seeded from env then managed in DB. Same trust boundary as the container host.

---

### Option E: Database with Encryption at Rest

Same as Option A, but credentials are encrypted in the database using a key derived from a secret.

```python
# Model
NodeRedConfig:
  nodered_password_encrypted: BinaryField
```

```bash
# .env or Django settings
FLOWHISTORY_ENCRYPTION_KEY=randomly-generated-key
```

**Pros:**
- Single source of truth (DB), managed via UI
- Credentials not readable by directly opening the SQLite file
- Encryption key can be injected via Docker secrets

**Cons:**
- Encryption key is still stored somewhere (env var or file) — if attacker has access to the volume, they likely have access to the env too
- Adds a dependency (e.g., `cryptography` library) or custom crypto code
- Key rotation is complex
- If the encryption key is lost, all credentials are unrecoverable
- Over-engineered for the threat model — this protects against someone copying just the SQLite file but not the container environment, which is an unlikely scenario for a home-lab
- Not automation-friendly

**Security assessment:** Provides defense-in-depth against a narrow threat (DB file exfiltration without env access). Not worth the complexity for a home-lab tool. The encryption key itself becomes the new secret to protect.

---

### Option F: Docker Secrets

Use Docker Swarm secrets or Compose `secrets` to inject credentials as files.

```yaml
# docker-compose.yml
services:
  flowhistory:
    secrets:
      - flowhistory_prod_pass
      - flowhistory_dev_pass

secrets:
  flowhistory_prod_pass:
    file: ./secrets/flowhistory_prod_pass.txt
  flowhistory_dev_pass:
    file: ./secrets/flowhistory_dev_pass.txt
```

FlowHistory reads from `/run/secrets/<name>` at runtime.

**Pros:**
- Docker-native secret management
- Secrets are tmpfs-mounted (not on disk inside the container)
- Familiar to Docker Swarm users

**Cons:**
- Requires Docker Compose v2 secrets support or Swarm mode
- Secret files are still plaintext on the host filesystem
- One secret file per credential — scales poorly
- Most home-lab users don't use Swarm
- Not automation-friendly without additional tooling

**Security assessment:** Marginally better in-container security (tmpfs vs env), but the source files are still plaintext on the host. Overkill for this use case.

## Comparison Matrix

| Criteria | A: DB Only | B: YAML | C: YAML+Env | D: Env+Auto+UI | E: DB+Encrypt | F: Docker Secrets |
|----------|-----------|---------|-------------|-----------------|---------------|-------------------|
| Implementation complexity | Low | Medium | High | Medium | High | Medium |
| UX (adding instances) | Simple (UI) | Edit file, restart | Edit 2 files, restart | Edit .env, restart (or UI) | Simple (UI) | Edit file, restart |
| Single source of truth | Yes (DB) | No (file+DB) | No (file+env+DB) | Mostly (DB+env) | Yes (DB) | Mostly (DB+files) |
| Credential security | Plaintext in DB | Plaintext in file | Plaintext in .env | Plaintext in .env | Encrypted in DB | Plaintext on host |
| Git-safe | Yes (DB not committed) | Risk (YAML with creds) | Yes (creds in .env) | Yes (creds in .env) | Yes | Yes |
| No restart for changes | Yes | No | No | No (new instances) | Yes | No |
| Automation-friendly | No | Yes | Yes | **Yes** | No | Partially |
| Manual-user-friendly | **Yes** | No | No | **Yes** | **Yes** | No |

## Decision

**Option D: Environment Variables with Auto-Discovery + UI Override.**

Rationale:
- **Automation-first**: Define instances entirely in `.env`, deploy with `docker compose up -d` — zero UI interaction needed
- **Manual-friendly**: Users can also add instances through the UI, or tweak env-seeded instances in the UI after deployment
- **Clean separation**: Credentials always in env vars (never in DB), config seeded from env then owned by DB
- **Standard patterns**: `.env` files are the Docker convention — no new concepts
- **No split-brain**: DB is authoritative for config state. Env vars are the seed (first creation) and the runtime credential source
- **Single file**: Everything in `.env` — no YAML, no secret files, no additional mounts

### Auto-Discovery Logic

On startup, FlowHistory scans environment variables:

1. Find all unique prefixes from `FLOWHISTORY_*_URL` and `FLOWHISTORY_*_FLOWS_PATH` patterns
2. For each prefix:
   - Has `_URL` → remote instance
   - Has `_FLOWS_PATH` but no `_URL` → local instance
   - Has both → log warning, treat as remote
3. For each discovered prefix, check if a `NodeRedConfig` with matching `env_prefix` exists in DB
4. If not → create one, populating fields from env vars (with model defaults for anything not set)
5. If yes → skip (don't overwrite UI edits)

### Credential Resolution

Credentials are **always** read from env vars at runtime, never stored in DB:

```python
def get_nodered_credentials(self):
    """Read credentials from environment variables using configured prefix."""
    if not self.env_prefix:
        return None, None
    prefix = self.env_prefix.upper()
    username = os.environ.get(f"FLOWHISTORY_{prefix}_USER", "")
    password = os.environ.get(f"FLOWHISTORY_{prefix}_PASS", "")
    return username, password
```

### Environment Variable Reference

Per-instance env vars use the pattern `FLOWHISTORY_{PREFIX}_{FIELD}`:

| Env Var | Required | Default | Maps to Model Field |
|---------|----------|---------|-------------------|
| `FLOWHISTORY_{P}_URL` | Yes (remote) | — | `nodered_url` + `source_type="remote"` |
| `FLOWHISTORY_{P}_FLOWS_PATH` | Yes (local) | — | `flows_path` + `source_type="local"` |
| `FLOWHISTORY_{P}_USER` | No | `""` | Runtime only (not stored) |
| `FLOWHISTORY_{P}_PASS` | No | `""` | Runtime only (not stored) |
| `FLOWHISTORY_{P}_NAME` | No | Titlecased prefix | `name` |
| `FLOWHISTORY_{P}_COLOR` | No | `""` | `color` |
| `FLOWHISTORY_{P}_SCHEDULE` | No | `"daily"` | `backup_frequency` |
| `FLOWHISTORY_{P}_TIME` | No | `"03:00"` | `backup_time` |
| `FLOWHISTORY_{P}_DAY` | No | `0` | `backup_day` |
| `FLOWHISTORY_{P}_MAX_BACKUPS` | No | `20` | `max_backups` |
| `FLOWHISTORY_{P}_MAX_AGE_DAYS` | No | `30` | `max_age_days` |
| `FLOWHISTORY_{P}_POLL_INTERVAL` | No | `60` | `poll_interval_seconds` |
| `FLOWHISTORY_{P}_WATCH` | No | `true` | `watch_enabled` |
| `FLOWHISTORY_{P}_DEBOUNCE` | No | `3` | `watch_debounce_seconds` |
| `FLOWHISTORY_{P}_ALWAYS_BACKUP` | No | `false` | `always_backup` |
| `FLOWHISTORY_{P}_BACKUP_CREDENTIALS` | No | `false` | `backup_credentials` |
| `FLOWHISTORY_{P}_BACKUP_SETTINGS` | No | `false` | `backup_settings` |
| `FLOWHISTORY_{P}_RESTART_ON_RESTORE` | No | `false` | `restart_on_restore` |
| `FLOWHISTORY_{P}_CONTAINER_NAME` | No | `"nodered"` | `nodered_container_name` |

### Settings UI Behavior

- **All instances** are configured via environment variables and shown as read-only in the settings UI.
- **Connection test button**: Validates URL + credentials work, shown for remote instances.
- UI-based instance creation is not currently supported; instances are added by setting env vars and restarting.

### Example Deployments

**Minimal — single local instance (required, no implicit defaults):**
```bash
# .env — at least one instance must be configured
FLOWHISTORY_LOCAL_FLOWS_PATH=/nodered-data/flows.json
```

**Multi-instance — fully automated:**
```bash
# .env
FLOWHISTORY_PROD_URL=http://192.168.1.50:1880
FLOWHISTORY_PROD_USER=admin
FLOWHISTORY_PROD_PASS=secretpass1
FLOWHISTORY_PROD_SCHEDULE=daily
FLOWHISTORY_PROD_TIME=03:00

FLOWHISTORY_DEV_URL=http://192.168.1.51:1880
FLOWHISTORY_DEV_USER=admin
FLOWHISTORY_DEV_PASS=secretpass2
FLOWHISTORY_DEV_SCHEDULE=hourly

FLOWHISTORY_LOCAL_FLOWS_PATH=/nodered-data/flows.json
FLOWHISTORY_LOCAL_NAME=Docker Host
```

**Hybrid — automation seeds, user tweaks:**
```bash
# .env — automation sets the basics
FLOWHISTORY_PROD_URL=http://192.168.1.50:1880
FLOWHISTORY_PROD_USER=admin
FLOWHISTORY_PROD_PASS=secretpass1
```
Then user adjusts schedule, retention, color, etc. via the settings UI.

## Consequences

### Positive
- Fully deployable via automation — define `.env` and deploy, no UI needed
- Manual users can still configure everything through the UI
- Credentials never in the database
- Standard Docker `.env` pattern — no new concepts
- Single file for all config (`env` → seed) + UI for refinement
- Compatible with Docker secrets / external secret injection for advanced users

### Negative
- Many possible env vars per instance (though all but one are optional)
- "First creation only" seeding requires documentation — changing an env var after first run won't update the DB
- Container restart needed for new instances
- Env var convention must be strictly followed
