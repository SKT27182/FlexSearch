# FlexSearch 1.0 major-upgrade deployment

This release is a coordinated, breaking cutover. The API, worker, Celery Beat
scheduler, frontend, database revision, and runtime configuration must be
deployed together. Never run the previous application against the upgraded
schema.

## Required runtime contract

`backend/.env.example` is the canonical variable catalog. In particular, use
`POSTGRES_URL`, `JWT_SECRET`, and `API_KEY`; the obsolete `DATABASE_URL`,
`JWT_SECRET_KEY`, and `LLM_API_KEY` names are not accepted.

Production requires `APP_ENV=production`, an HTTPS `APP_PUBLIC_URL`, a strong
new `JWT_SECRET`, a strong `OPERATIONS_TOKEN`, verified TLS for OpenSearch,
non-default datastore credentials, and explicit CORS origins. Startup rejects
an insecure configuration. Credentials must be injected at runtime and must
not be baked into images.

The supported direct-upload formats are PDF, plain text, Markdown, HTML, PNG,
and JPEG. DOCX, PPT/PPTX, CSV, XLS, and XLSX are not supported in this release.
The backend enforces upload, remote response, archive, page, and decoded-image
limits listed in `.env.example`.

## Cutover runbook

1. Announce maintenance and stop uploads, crawls, chat, graph rebuilds, API
   instances, workers, and schedulers.
2. Back up PostgreSQL, MinIO, Neo4j, OpenSearch indexes, and Microsoft GraphRAG
   workspaces. Test restoration before proceeding.
3. Run a preflight inventory for invalid RAG configurations, missing/orphaned
   objects, incomplete documents, and embedding model/dimension drift.
4. Supply `POSTGRES_ADMIN_URL` only to the migration environment and run
   `make db-bootstrap`. It creates the application database when absent,
   applies `alembic upgrade head`, and verifies revision `009`. The migration
   identity needs `CREATE DATABASE` and schema DDL privileges. Remove
   `POSTGRES_ADMIN_URL` before starting API or worker runtimes. Migration `009`
   is forward-only.
5. Rotate `JWT_SECRET`. This intentionally signs every user out.
6. Deploy the API, worker, scheduler, and frontend from the same release.
   Runtime database credentials should not have CREATE/ALTER/DROP privileges.
7. Reconcile document/object state and reindex invalid Neo4j anchors and every
   index whose embedding model or vector dimension changed.
8. Run authorization, upload, retrieval, chat, streaming, observability, and
   migration smoke tests.
9. Enable read traffic, then chat, then ingestion, then background reindexing.
10. Monitor outbox backlog/failures, worker memory, rejected uploads, graph
    lease contention, stale-generation exits, invalid citations, and cleanup.

Application rollback requires restoring the complete pre-upgrade data snapshot
and the old deployment together.

## Process topology

The provided `docker-compose.yml` starts:

- `backend`: FastAPI, with `/health/live` as process liveness.
- `worker`: Celery queues `ingest,graph,summary,default`.
- `scheduler`: Celery Beat, required to dispatch transactional outbox events.
- `frontend`: the static React build with a restrictive Content Security Policy.

Apply migrations before starting these processes. API startup verifies that
PostgreSQL is exactly at Alembic revision `009` and performs no
DDL.

## Observability

`GET /health/live` is public and contains process status only. Send
`Authorization: Bearer $OPERATIONS_TOKEN` to `/health/ready` and `/metrics`.
Neither health response includes exception strings, credentials, usage data,
or internal topology. Detailed failures remain in protected logs.

## Authentication and interface changes

Authentication uses a 15-minute access token held only in browser memory. A
reload, closed tab, new tab, password change, role change, disablement, or
administrative reset requires login. There is no refresh-token API.

RAG mode changes return HTTP 202 with a new generation and transition status.
Retrieval is served only from the published generation. `/health` has been
removed. Invalid chunk settings return 422, excessive content returns 413, and
unauthorized chat session identifiers return 404.
