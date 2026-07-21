# Authentication & Access Control

Code-backed reference for FlexSearch auth, roles, JWT, infra-hub linking, and project ACL. Primary modules: `app/core/security.py`, `app/core/dependencies.py`, `app/services/auth_login.py`, `app/services/infra_hub_users.py`, `app/services/project_access.py`, `app/api/auth.py`, `app/api/admin.py`.

**In plain language:** FlexSearch proves who you are at login (password → short-lived JWT), then on every protected request reloads your user row and asks “are you allowed to touch this project / admin target?” There is no org/tenant table — isolation is **owner-of-project**, with a separate admin ladder for cross-user ops. Platform operators authenticate against infra-hub (`main_db`) and are **linked** into FlexSearch, not duplicated.

---

## 0. Concepts & terminology

### Authentication vs authorization

Two different questions, answered at different layers:

| Term | Question | Meaning here | Typical HTTP result |
|------|----------|----------------|---------------------|
| **Authentication** | “Who are you?” | Prove identity — email/password at login, then a Bearer JWT on later requests | `401` if missing/invalid |
| **Authorization** | “What may you do?” | After identity is known, check role and project ownership | `403` if known but not allowed |

FlexSearch separates these cleanly: `get_current_user` **authenticates**; helpers like `require_admin` and `user_can_access_project` **authorize**.

**Worked examples**

| Situation | Authn | Authz | Result |
|-----------|-------|-------|--------|
| No `Authorization` header on `GET /api/projects` | fails | never runs | `401` |
| Valid JWT, but Alice’s token used on Bob’s `project_id` | passes | `owner_id != user.id` | `403 Not authorized` |
| Valid JWT, Alice opens Alice’s project | passes | owner match | `200` |
| Valid `USER` JWT on `GET /api/admin/users` | passes | `require_admin` fails | `403` |
| Valid `ADMIN` JWT on admin list of a `USER`’s projects | passes | `user_can_administer_target` OK | allowed on `/api/admin/*` only |

Rule of thumb: **401 = we don’t know you; 403 = we know you, and you may not.**

### JWT mental model

A **JWT** (JSON Web Token) is three Base64url segments: `header.payload.signature`. FlexSearch signs with `JWT_SECRET` (`HS256` by default via `python-jose`). The payload is **readable** to anyone who holds the token — signing proves the server issued it; it does **not** encrypt claims.

Typical FlexSearch claims after login:

| Claim | Meaning |
|-------|---------|
| `sub` | FlexSearch `User.id` (UUID string) — the only identity the API trusts from the token |
| `role` | Snapshot of role at issue time — useful for clients; **not** used for server ACL |
| `exp` | Expiry (`JWT_EXPIRE_MINUTES`, default 60) |

**Mental model for each request**

```
Client sends Bearer JWT
        │
        ▼
decode_access_token  →  valid signature + not expired?
        │ yes
        ▼
Load User where id == UUID(sub)   ← Postgres is source of truth
        │
        ▼
Authorize (owner? admin? administer target?)
```

Why reload the row? Authorization uses live database state and verifies the JWT's `token_version`. Password resets, role changes, disablement, and administrative resets increment that version and revoke every outstanding access token immediately.

There are **no refresh tokens**. Access tokens expire after 15 minutes and exist only in browser memory, so reload or closing the tab requires login. Logout clears all client state; security-sensitive account changes revoke tokens through `token_version`.

### Bearer tokens

**Bearer** means “whoever presents this token is treated as the subject” — like a temporary badge. The client sends:

```http
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

| Credential | Used for | Not used for |
|------------|----------|--------------|
| **Password** | Login only; new hashes use Argon2id and legacy bcrypt is rehashed after one successful login | Sent on every API call |
| **JWT access token** | Proving identity on protected routes (`Authorization: Bearer …`) | Long-lived API keys or refresh |
| **HTTP Basic** | Swagger/OpenAPI docs only (`/api/docs`) | Normal product APIs |
| **Chat session** | Conversation history scoped to `(project_id, user_id)` | Login state |

There are **no product API keys**. Protect the JWT like a password for its lifetime: HTTPS in transit, do not log it, do not put it in URLs.

“Session” in chat/job docs means a **chat conversation** (Postgres + optional Redis memory), not an auth session cookie.

### bcrypt (password hashing)

FlexSearch never stores plaintext passwords. `passlib` **bcrypt** (`verify_password` / `get_password_hash` in `app.core.security`) turns a password into a one-way hash with a per-hash salt. Verification re-hashes the candidate and compares — you cannot “decrypt” the stored value back to the password.

| Idea | In this codebase |
|------|------------------|
| Hash at rest | `User.hashed_password` (local users) or `main_db.users.hashed_password` (infra-hub) |
| 72-byte limit | bcrypt truncates input to 72 bytes before hash/verify (same truncation on both sides) |
| Infra-linked rows | Local hash is a random unusable value — login always verifies against `main_db` |

Example path: register/login with password `secret` → store `$2b$…` hash → later login calls `verify_password("secret", hash)` → never compare strings of the raw password to the DB column.

### RBAC (role-based access control)

**RBAC** means permissions follow a **role**, not a per-resource ACL matrix of arbitrary grants. FlexSearch has three roles in a strict ladder:

```text
INFRA_ADMIN  >  ADMIN  >  USER
```

| Role | Who gets it | What it unlocks |
|------|-------------|-----------------|
| `USER` | Public register / admin create | Own projects on normal APIs |
| `ADMIN` | Created by `INFRA_ADMIN` | `/api/admin/*` over **USER** targets only; still owner-only on normal project routes |
| `INFRA_ADMIN` | Linked from infra-hub | `/api/admin/*` over `ADMIN` + `USER`; platform password lives in `main_db` |

This is **not** fine-grained “Alice can edit doc X.” Day-to-day resource access is **ownership**; roles mainly gate **admin** surfaces and who may administer whom (`user_can_administer_target` — strictly lower tiers only; never `INFRA_ADMIN` as a target).

### Owner-only isolation vs org tenancy

Many SaaS products model a **tenant/org**: members share a workspace; authz is “are you in this org (and what org-role)?” FlexSearch does **not** have an org/tenant table.

| Model | Isolation unit | Typical check |
|-------|----------------|---------------|
| **Org / multi-tenant** (not FlexSearch) | Organization membership | `user.org_id == resource.org_id` (+ org role) |
| **Owner-only** (FlexSearch normal APIs) | Single `Project.owner_id` | `project.owner_id == user.id` |

Isolation here:

- **User** — identity and role.
- **Project** — owned by one user; documents, chat, jobs, crawl, and bulk inherit that ownership check.
- **Chat session** — further scoped to `(project_id, user_id)` so two users never share a thread even if (hypothetically) they shared a project — which normal APIs do not allow anyway.

**Example:** Alice and Bob are both `USER`s. Alice’s project is invisible on Bob’s `GET /api/projects` and returns `403` if Bob guesses the UUID. An `ADMIN` also cannot open Alice’s project via `/api/projects/{id}` — they must use `/api/admin/*` after the role-ladder check. Think “per-user project ownership,” not “company workspace with seats.”

### Infra-hub linking (concept)

**Infra-hub** is the shared platform identity store (`main_db.users`). FlexSearch does not copy platform passwords into its own DB for operators. Instead it **links**:

```
main_db.users (id, email, hashed_password, is_active)
        │  verify on login (read-only)
        ▼
FlexSearch users row
  role = INFRA_ADMIN
  infra_hub_user_id = main_db id
  hashed_password = random unusable hash
```

| Step | What happens |
|------|----------------|
| First successful infra-hub login | `get_or_create_infra_linked_user` creates/updates the FlexSearch row |
| Later logins | Password checked only in `main_db`; if `infra_hub_user_id` is set, local password login is rejected |
| Profile / password APIs | Forbidden for linked accounts (`403`) — change credentials in infra-hub, not FlexSearch |

**Example:** Platform admin `ops@example.com` exists only in `main_db`. On `POST /api/auth/login`, FlexSearch verifies bcrypt against `main_db`, then issues a JWT whose `sub` is the **FlexSearch** user UUID. The link field keeps one platform identity mapped to one app user without duplicating the real password hash.

### How auth works (end-to-end mental model)

1. **Login** — credentials checked against infra-hub (`main_db`) first, then FlexSearch-local users.
2. **Token** — server issues a JWT whose `sub` is the FlexSearch `User.id`.
3. **Each request** — route opts in with `Depends(get_current_user)` (or stricter); JWT is decoded, then the **full `User` row is reloaded** from Postgres.
4. **Resource access** — normal APIs allow only the **project owner**; admins manage other users’ data only via `/api/admin/*`.

### Why this approach

- **Opt-in per route** (no global auth middleware) — explicit `Depends(...)` keeps public vs protected surfaces obvious.
- **DB as source of truth for roles** — JWT carries `role` for clients, but ACL decisions use the live `User` row so role changes apply without re-login.
- **Owner-only normal APIs** — admins do not silently inherit every user’s projects on day-to-day routes; cross-user ops go through admin endpoints with a strict role ladder.
- **Infra-hub link for platform operators** — `INFRA_ADMIN` passwords stay in `main_db`; FlexSearch stores a link (`infra_hub_user_id`) and an unusable local hash so platform identity is not duplicated.

---

## Role hierarchy

RBAC ladder (see [§0 RBAC](#rbac-role-based-access-control)): permissions follow role, not per-document grants.

```text
INFRA_ADMIN  >  ADMIN  >  USER
```

| Role | Source | Meaning |
|------|--------|---------|
| `INFRA_ADMIN` | infra-hub `main_db.users` (read-only verify) | Platform operator; linked into FlexSearch via `users.infra_hub_user_id` |
| `ADMIN` | FlexSearch-local (created by infra admin) | FlexSearch-scoped administrator |
| `USER` | FlexSearch-local (`POST /api/auth/register` or admin create) | Standard project owner |

Enum: `app.db.models.UserRole`.

Helpers:

- `app.core.dependencies.is_infra_admin` / `is_flexsearch_admin` / `has_admin_access`
- `app.services.project_access.user_can_administer_target(admin, target)` — admins may manage **strictly lower** tiers only; never `INFRA_ADMIN`

Higher roles can administer lower ones on **admin** routes only. On **normal** project routes, even an `ADMIN` is just another owner of their own projects — they cannot open someone else’s project without going through `/api/admin/*`.

**Example:** `ADMIN` may `DELETE /api/admin/projects/{id}` for a `USER`-owned project after `user_can_administer_target`; the same admin calling `GET /api/projects/{id}` for that project still gets `403` — normal routes stay owner-only.

---

## Login flow

```mermaid
flowchart TD
  A[POST /api/auth/login<br/>OAuth2PasswordRequestForm] --> B{authenticate_user}
  B -->|1| C[verify_infra_hub_credentials<br/>asyncpg → main_db.users]
  C -->|active + bcrypt OK| D[get_or_create_infra_linked_user]
  D --> E[User.role = INFRA_ADMIN<br/>unusable local password hash]
  C -->|miss / inactive / bad pw| F[Select FlexSearch users by email]
  F -->|infra_hub_user_id set| G[Reject — must use main_db]
  F -->|local + verify_password| H[USER or ADMIN]
  E --> I[create_access_token sub + role]
  H --> I
  I --> J[Token access_token bearer]
```

Login always tries **infra-hub first**, then falls back to local FlexSearch users. Linked accounts must authenticate against `main_db` (local password login is rejected if `infra_hub_user_id` is set).

### Infra-hub path (`app.services.infra_hub_users`)

- Connects with `settings.infra_hub_postgres_url` (same host/creds as FlexSearch Postgres by default, database `INFRA_HUB_POSTGRES_DB` = `main_db`).
- Reads `id, email, name, hashed_password, is_active` — **no password copy** into FlexSearch.
- Linked FlexSearch row gets `hashed_password = get_password_hash(secrets.token_urlsafe(32))` (unusable for local login).
- Profile/password changes for linked accounts are **forbidden** in FlexSearch (`403` on `/api/auth/me/profile` and `/me/password`).

### Local path

- Public register always creates `UserRole.USER`.
- Admin create (`POST /api/admin/users`) may set `ADMIN` or `USER`; only `INFRA_ADMIN` may create `ADMIN`.

---

## JWT

Implementation detail for the [JWT mental model](#jwt-mental-model) above. FlexSearch uses a short-lived **Bearer** access token after login — not as an API key and not as a refreshable session.

| Item | Implementation |
|------|----------------|
| Library | `python-jose` (`app.core.security`) |
| Algorithm | `JWT_ALGORITHM` (default `HS256`) |
| Secret | `JWT_SECRET` (required) |
| Expiry | `JWT_EXPIRE_MINUTES` (default `60`) |
| Claims | `sub` = user UUID string, `role` = role value, `exp` |

**Authorization always reloads `User` from Postgres** via `get_current_user` (`decode_access_token` → `User.id == UUID(sub)`). The JWT `role` claim is **not** used for ACL decisions — role changes in DB apply on the next request without re-login.

Password hashing: `passlib` bcrypt, passwords truncated to 72 bytes (`verify_password` / `get_password_hash`) — see [bcrypt](#bcrypt-password-hashing).

---

## FastAPI dependencies

These are the hooks routes use to enforce “must be logged in” / “must be admin”:

| Dependency | Behavior |
|------------|----------|
| `oauth2_scheme` | `OAuth2PasswordBearer(tokenUrl="/api/auth/login")` |
| `get_current_user` | Decode JWT → load `User` or `401` |
| `get_current_active_user` | Alias of `get_current_user` (FlexSearch `User` has **no** `is_active` column) |
| `require_admin` | `INFRA_ADMIN` or `ADMIN` else `403` |
| `require_infra_admin` | `INFRA_ADMIN` only else `403` |

There is **no** global auth middleware — each route opts in via `Depends(...)`.

---

## Project ACL (normal vs admin routes)

**ACL** here means the rules for who may touch a resource — not a stored per-row permission list. Combined with [owner-only isolation](#owner-only-isolation-vs-org-tenancy) and [RBAC](#rbac-role-based-access-control): day-to-day APIs check **ownership**; admin APIs check **role ladder**.

### Normal APIs (`/api/projects`, documents, chat, retrieval, crawl, bulk, …)

```python
# app.services.project_access
def user_can_access_project(user, project) -> bool:
    return project.owner_id == user.id  # owners only

def user_owns_project(user, project) -> bool:
    return project.owner_id == user.id
```

- **Owners only** for read/write on project-scoped resources.
- Admins **cannot** open another user’s project via these routes (`403 Not authorized`).
- List endpoints (`GET /api/projects`) filter `Project.owner_id == current_user.id`.

### Admin APIs (`/api/admin/*`)

- Require `require_admin` (or `require_infra_admin` for role changes).
- Cross-user resource ops call `user_can_administer_target` before mutate/list:
  - `GET /admin/users/{id}/projects`
  - `DELETE /admin/projects/{id}`
  - `DELETE /admin/documents/{id}` (if owner resolvable)
- Infra-hub accounts cannot be password-reset, role-changed, or deleted from FlexSearch.
- FlexSearch `ADMIN` cannot create/delete other `ADMIN`s — only `INFRA_ADMIN` can.

```mermaid
flowchart LR
  subgraph normal [Normal routes]
    O[Owner] -->|CRUD| P[Own projects]
    A1[ADMIN] -.->|403| P2[Others projects]
  end
  subgraph admin [Admin routes]
    IA[INFRA_ADMIN] -->|manage| AU[ADMIN + USER resources]
    FA[ADMIN] -->|manage| U[USER resources only]
  end
```

---

## Chat session ACL

Chat “sessions” are conversation threads, not login sessions.

- Sessions are scoped by `(project_id, user_id)`.
- `ChatHistoryService` loads sessions with `user_id=current_user.id`.
- Project access is re-checked via `_load_accessible_project` (owner-only).
- Deleting a session also clears Redis session memory (`SessionMemoryService.clear`).

---

## Job SSE ACL

`GET /api/jobs/{job_id}/events` (`app.api.jobs._authorize_job_sse`):

1. Load job meta from Redis (`get_job_meta`).
2. Require `project_id` on meta.
3. `verify_project_access` (owner-only).

Jobs without project scope → `403`. Same ownership model as projects: you only stream events for jobs under projects you own.

---

## OpenAPI / docs auth

`GET /api/docs` and `/api/openapi.json` use **HTTP Basic** (`get_docs_auth` in `app.main`):

- Username = FlexSearch `users.email`
- Password verified against `users.hashed_password`

**Caveat:** infra-linked `INFRA_ADMIN` rows use an unusable password hash, so Basic docs login typically **fails** for those accounts. Use a local `ADMIN`/`USER` for Swagger, or call the API with Bearer tokens.

---

## Auth API surface

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| POST | `/api/auth/register` | none | Always `USER` |
| POST | `/api/auth/login` | none | Form: `username`=email, `password` |
| GET | `/api/auth/me` | Bearer | |
| PATCH | `/api/auth/me/profile` | Bearer | Local only |
| POST | `/api/auth/me/password` | Bearer | Local only |

Admin user management: see [backend README API map](../../README.md#api-surface-map) and `app/api/admin.py`.

---

## Security-related settings

| Env | Purpose |
|-----|---------|
| `JWT_SECRET` | Signing key (required) |
| `JWT_ALGORITHM` | Default `HS256` |
| `JWT_EXPIRE_MINUTES` | Fixed production policy: `15` |
| `INFRA_HUB_POSTGRES_DB` | Default `main_db` |
| `INFRA_HUB_POSTGRES_URL` | Optional override; else derived from `POSTGRES_URL` with `main_db` |
| `CRAWL_BLOCK_PRIVATE_URLS` | SSRF guard for crawl URLs (related ops hardening) |
| `RATE_LIMIT_*` | Per-user/IP limits on chat/crawl/bulk/suggestions |

---

## Authorization invariants

1. Every protected request loads an active user and validates `token_version`.
2. INFRA_ADMIN may administer ADMIN and USER; ADMIN may administer USER only.
3. Out-of-scope administrative targets and chat sessions return 404.
4. Session ownership is checked against both user and project before any Redis, history, retrieval, or LLM work.
5. The frontend never persists authentication material.
