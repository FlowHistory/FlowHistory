# ADR 0023: CI/CD Pipeline with GitHub Actions

## Status
Implemented

## Context

FlowHistory has no automated CI/CD pipeline. Tests are run manually inside the Docker container, and Docker images are built locally. As the project moves to the FlowHistory GitHub organization (`github.com/FlowHistory/FlowHistory`), we need automated testing on every PR and push, plus automated Docker image builds published to a container registry so users can pull pre-built images.

### Design Constraints

- Tests must run inside Docker to match the production environment (Python 3.13, system deps, Tailwind build)
- Images must be published to GitHub Container Registry (GHCR) under the FlowHistory org
- PR images should be tagged for easy testing but cleaned up after merge/close
- Release images need a `latest` tag for users who want to track stable releases
- Fork PRs must not be able to push images to the registry

## Decision

### 1. Workflow Structure

Five workflow files following the pattern established by [latchpoint/latchpoint](https://github.com/latchpoint/latchpoint):

| Workflow | File | Trigger |
|----------|------|---------|
| Tests | `tests.yml` | Reusable (`workflow_call`) |
| CI | `ci.yml` | Pull requests |
| Build and Push | `build-and-push.yml` | Push to `main` or `v*` tags |
| Release | `release.yml` | GitHub Release published |
| Cleanup PR Image | `cleanup-pr-image.yml` | PR closed |

`tests.yml` is a reusable workflow called by both `ci.yml` and `build-and-push.yml`, ensuring the same test suite gates both PR checks and production builds.

### 2. Image Tagging Strategy

All images publish to `ghcr.io/flowhistory/flowhistory`.

| Trigger | Tags |
|---------|------|
| Pull request | `pr-<number>`, `sha-<commit>` |
| Push to `main` | `main`, `sha-<commit>` |
| Push `v*` tag | `<tag>` (e.g. `v1.2.3`), `sha-<commit>` |
| GitHub Release | Adds `latest` alias to the release tag |

Every build includes a `sha-<commit>` tag for traceability. PR images are ephemeral — cleaned up when the PR closes.

### 3. Test Job

The reusable `tests.yml` workflow:

1. Writes a minimal `.env` for CI (SQLite, debug mode)
2. Builds the `flowhistory` service via `docker compose build`
3. Runs `python manage.py test backup -v2` inside the container
4. Cleans up with `docker compose down -v --remove-orphans`

This matches the local development workflow — tests run inside the same Docker image that ships to production.

### 4. Security

- Fork PRs cannot push images: gated by `if: github.event.pull_request.head.repo.full_name == github.repository`
- GHCR authentication uses `GITHUB_TOKEN` (automatic, no secrets to manage)
- PR image cleanup uses the GitHub API to delete package versions, trying org endpoint first then user

### 5. Build Caching

All build workflows use GitHub Actions cache (`cache-from: type=gha`, `cache-to: type=gha,mode=max`) to speed up Docker layer caching across runs.

### 6. Release Flow

The release workflow uses `crane` to tag an existing image as `latest` rather than rebuilding. This ensures the `latest` tag points to the exact same image that was tested. Includes retry logic (5 attempts, 30s apart) in case the build-and-push workflow hasn't finished yet.

### Files Added

| File | Purpose |
|------|---------|
| `.github/workflows/tests.yml` | Reusable test workflow — builds image and runs Django tests |
| `.github/workflows/ci.yml` | PR workflow — tests then builds/pushes `pr-N` image |
| `.github/workflows/build-and-push.yml` | Main/tag workflow — tests then builds/pushes `main` or version image |
| `.github/workflows/release.yml` | Release workflow — tags published release as `latest` |
| `.github/workflows/cleanup-pr-image.yml` | PR cleanup — deletes `pr-N` image from GHCR on close |

## Alternatives Considered

### Build Without Docker Compose for Tests
Rejected. The production Dockerfile includes a Tailwind CSS build step and specific system dependencies. Running tests outside Docker risks environment drift between CI and production.

### Docker Hub Instead of GHCR
Rejected. GHCR integrates natively with GitHub — authentication uses `GITHUB_TOKEN`, no extra secrets needed, and image visibility ties to repository permissions.

### Single Workflow File
Rejected. Separate workflows keep concerns isolated (testing, building, releasing, cleanup) and allow different triggers with different concurrency groups.

## Consequences

**Positive:**
- Every PR is automatically tested before merge
- Pre-built Docker images available on GHCR for every commit, PR, and release
- `latest` tag gives users a stable pull target
- PR images enable easy testing of in-progress work
- Automatic cleanup prevents GHCR storage bloat from PR images
- No secrets to manage — `GITHUB_TOKEN` handles everything

**Negative:**
- CI builds take longer than local tests due to Docker layer rebuilds (mitigated by GHA cache)
- GHCR storage grows with each push to `main` (sha-tagged images accumulate)
