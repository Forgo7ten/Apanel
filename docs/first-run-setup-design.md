# First-run Web setup: implementation plan and three reviews

## Acceptance criteria

Only an uninitialized, freshly migrated Apanel instance can complete Web setup.
The deployment owner starts Compose, reads `.apanel-setup-token` in the
repository root, opens `/setup`, and submits the token plus the first
administrator's username, email and password. No administrator credentials
belong in `.env`. The server responds without credentials and the first
administrator logs in through the normal login page.

After setup succeeds, the database permanently records completion and
irreversibly clears the token hash. The root file can remain, but cannot be
used again. Restarting, deleting that file, editing `.env` and removing the
administrator do not reopen setup.

## Sequence / trust boundaries

1. `migrate` establishes the canonical fresh-only schema.
2. A one-off `admin-bootstrap` container invokes `prepare-setup`, mounts the
   repository root and obtains a PostgreSQL advisory transaction lock shared
   with the manual administrator creation path.
3. If completed, the job exits without touching the token file. If a legacy
   administrator already exists, it records completion. Otherwise it generates
   a `secrets.token_urlsafe(48)` token prefixed `apanel_setup_`.
4. Only the SHA-256 token hash is committed in `installation_setup`. A
   temporary 0600 file in the same host directory is atomically renamed to
   `.apanel-setup-token` and chowned to the project-directory owner when
   necessary. Compose does not give Backend/Frontend that bind mount.
5. `GET /api/v1/setup/status` returns only `NOT_READY`, `PENDING`, or
   `COMPLETED`. The setup page is only usable while pending; login, register,
   and workspace guards redirect to `/setup` while pending.
6. `POST /api/v1/setup/complete` is rate limited through a shared Redis
   counter; the API refuses writes if Redis is unavailable. The service locks
   the initialization state, compares the token hash in constant time, creates
   one Argon2id administrator and marks completion in one transaction.
7. The root file remains until an operator deletes it. No automatic
   administrator creation or password reset follows.

## Review 1: database, concurrency, and replay

**Rejected:** deciding readiness solely by `users.role == ADMIN`. Removing a
user could reopen installation; concurrent requests could both win.

**Chosen:** single durable primary-key row `installation_setup(id=1)`.
Completed state persists independently of users. Existing PostgreSQL
advisory lock serializes preparation, Web setup and manual bootstrap.
The User insert and token consumption occur in the same transaction. A
replayed request returns conflict; invalid tokens return forbidden.

**Tests:** initial/ready/completed status, successful administrator login
hash, invalid token, one-time replay, deletion of administrator, manual
bootstrap. PostgreSQL concurrency is covered by the shared locking mechanism
and should also be rerun against an isolated test PostgreSQL database.

## Review 2: host permissions, restart and recovery

**Rejected:** mounting the entire host repository into the long-running
Backend, passing an admin password in Compose env, logging secrets, or
silently replacing a lost pending token on every restart.

**Chosen:** only the one-off task mounts the repository RW. It creates the
token with 0600 mode and verifies existing file ownership and mode before
reusing. Normal restart is idempotent. A missing or mismatched pending file
fails closed; the deployment operator can explicitly run:

```sh
docker compose run --rm admin-bootstrap python -m app.cli prepare-setup \
  --output /setup-root/.apanel-setup-token --rotate
```

The previous token stops working after the new hash is committed. A failure
after the file rename but before the DB commit can leave a stale file;
rotation is the documented recovery path. The project root must be on a
filesystem that supports atomic same-directory renames and reasonable file
permissions. Initial setup from a public network requires HTTPS.

## Review 3: API/UX/abuse prevention

**Rejected:** putting tokens into URL query strings or fragments, returning a
token from a public endpoint, automatic token display on the login screen,
client-side-only enforcement, and permissive attempts during Redis failures.

**Chosen:** separate API models, no-cache status endpoint, 77-character token
validation, password confirmation in the browser, shared per-installation
Redis 30/minute attempt budget and fail-closed errors. The server remains the
sole authority on setup completion. Login and registration remain unchanged
after completion, and successful setup does not issue a browser session.

## Verification checklist

- [ ] `pytest` backend, including isolated PostgreSQL concurrency/migration.
- [ ] `ruff check .` and `alembic check`.
- [ ] Frontend `npm test`, `npm run typecheck`, `npm run lint`,
      `npm run build`.
- [ ] `docker compose config -q`.
- [ ] Fresh Docker install + real `/setup` smoke test.
- [ ] Real second startup, old-token replay, `0600` host ownership, loss
      and explicit rotation, deleting admin, Redis-failure handling.

The environment running the review may be unable to execute Docker or
install the project runtime. Failed/unavailable checks must be reported
as unverified, not as passes.
