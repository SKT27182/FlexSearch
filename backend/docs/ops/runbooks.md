# FlexSearch Runbooks

Short incident playbooks for operators. Pair with [ops README](./README.md) and [Celery](../celery/README.md).

---

## R1 — Empty retrieval rate spike

**Symptoms:** `/health` → `metrics.empty_retrieval_rate` rising; users see “could not find relevant information”; `flexsearch_chat_empty_retrieval_total` / `flexsearch_retrieval_empty_total` climbing.

**Checks**

1. `GET /metrics` → compare `flexsearch_retrieval_empty_total` vs `flexsearch_retrieval_requests_total` (and chat empty vs chat requests).
2. OpenSearch health: `curl -s "$OPENSEARCH_URL/_cluster/health"`.
3. Project has documents with `status=completed` and expected chunk/summary levels in OpenSearch.
4. Chat stages: multi-query / rewrite may over-narrow — try Search lab / raw retrieve with the same query.
5. Confirm embedding model/dimension still matches the index mapping after config changes.

**Mitigations**

- Reindex project documents; rebuild summaries if hierarchy retrieval is enabled.
- Temporarily lower or disable `chat.multi_query` / clarify / multihop.
- Fix OpenSearch connectivity or mapping drift, then reindex.

---

## R2 — Celery backlog / stuck ingest

**Symptoms:** Documents stuck in `stored` / `extracting` / `indexing` / `graph_indexing`; crawl/bulk SSE idle or progress frozen; worker logs quiet.

**Checks**

```bash
# Worker alive and consuming all queues
docker compose logs -f worker
# or: make worker-local

# Redis (broker + SSE)
redis-cli -h 127.0.0.1 -p 63791 -a "$REDIS_PASSWORD" ping

# Optional: Celery inspect from backend venv
cd backend && .venv/bin/celery -A app.celery_app inspect active
cd backend && .venv/bin/celery -A app.celery_app inspect reserved
```

Confirm queues: `ingest`, `graph`, `summary`, `default`. Confirm `CELERY_TASK_ALWAYS_EAGER=false` in prod.

**Mitigations**

- Start/restart worker; ensure `-Q` includes the backlog queue.
- Raise `CELERY_CONCURRENCY` or split workers by queue (OCR ingest vs graph/summary/default).
- Retry document: `POST /api/projects/{id}/documents/{doc}/retry` (force full extract).
- Project reindex API for bulk stuck docs.
- If logs show `Discarding revoked task`, a bad revoke+reuse happened — re-enqueue via schedule helpers (fresh task id); do not manually reuse revoked ids.

---

## R3 — OpenSearch unreachable

**Symptoms:** `/health` degraded; retrieval/chat errors; ingest fails at indexing.

**Checks**

```bash
curl -s "$OPENSEARCH_URL"
# host often http://127.0.0.1:9200 ; containers http://opensearch:9200
```

**Mitigations**

- Restart infra-hub OpenSearch; do not change hub config from FlexSearch.
- Verify `OPENSEARCH_URL` for host vs container matrix.
- After recovery, reindex affected projects if writes failed mid-flight.

---

## R4 — SSRF / crawl blocked

**Symptoms:** Crawl returns 400 “Unsafe crawl URL”; bulk URL refs fail.

**Expected** when targeting private IPs / localhost with `CRAWL_BLOCK_PRIVATE_URLS=true`.

**Mitigations**

- Use public documentation hosts only.
- For internal staging only: set `CRAWL_BLOCK_PRIVATE_URLS=false` (never in production).

---

## R5 — Rate-limit storm (429)

**Symptoms:** Clients get 429 on chat/crawl/bulk/suggestions; `flexsearch_rate_limit_hits_total{rule=...}` rising; `Retry-After` header present.

**Checks**

- Which rule: `chat` | `crawl` | `bulk` | `sensitive`.
- Redis up (shared window across API replicas); if Redis down, each process has its own memory window (limits look “looser” or inconsistent).

**Mitigations**

- Raise `RATE_LIMIT_*_PER_MINUTE` for the affected rule, or set that rule to `0` temporarily.
- Check for runaway UI polling / scripts; validate with `backend/scripts/load_smoke.py`.
- Distinguish API 429 from GraphRAG provider rate limits during graph rebuild (separate `GRAPHRAG_RATE_LIMIT_*` settings / worker logs).

---

## R6 — Job SSE 403 / 404

**Symptoms:** Crawl/bulk progress UI cannot subscribe.

**Checks**

- Job meta TTL is **6h** — expired jobs → 404.
- Wrong user (not project owner) → 403.
- Bulk jobs without `target_project_id` may lack Redis meta — always import into a project.
- Redis down → meta may only exist in the scheduling process memory fallback.

**Mitigations**

- Re-submit job; ensure logged-in user owns the project.
- Confirm Redis is up so meta registration persists across API processes.

---

## R7 — Summary COMPLETED-with-error

**Symptoms:** Document `status=completed`, `progress_pct=100`, but `error_message` starts with `summary:` and/or `processing_step` is “Summaries failed (chunks still searchable)”. Chat still finds chunks; hierarchical / summary-level retrieval may be weak or empty.

**Checks**

1. Worker logs for `build_document_summaries_task` / `summary_worker`.
2. Project is **vector** mode with `summaries.enabled` (graph / MS GraphRAG skip summaries by design).
3. OpenSearch still has base chunks for the document.

**Mitigations**

- Reindex the document or project (cancels and reschedules summary after vector COMPLETED).
- Fix LLM/API key / quota issues that caused the summary failure, then re-run.
- Treat as non-fatal for keyword/vector chunk search; do not mark the whole ingest failed unless chunks are missing.

---

## R8 — Stuck graph indexing

**Symptoms:** Project `graph_index_status.status == "indexing"` for a long time; UI spinner never clears; Microsoft GraphRAG rebuild or Neo4j per-doc path appears hung.

**Checks**

1. Worker consuming **`graph`** (and **`ingest`** for Neo4j per-doc): `docker compose logs worker`.
2. Celery: `inspect active` for `rebuild_graph_index_task` / `process_document_task`.
3. API startup already ran `reconcile_interrupted_graph_indexes()` — refresh graph status API (triggers `reconcile_stale_graph_index` where wired).
4. Stale timeout is ~70 minutes from `indexing_started_at` when the Celery task is dead.
5. Multi-worker: process-local `_in_flight` does not block other processes — look for overlapping rebuilds in logs.

**Mitigations**

- Click **Rebuild** (or schedule rebuild with `debounce_seconds=0`) after status reconciles to `failed`.
- Ensure only one graph consumer if duplicate builds appear.
- For Neo4j mode-switch wipe failures, check whether `wipe_neo4j_graph` → `delete_project_graph` AttributeError appears in logs (method should be `delete_project_subgraph`); wipe/fix manually if needed, then rebuild.
- Confirm `API_KEY` is set for Graph RAG paths.

---

## R9 — LLM / token budget

**Symptoms:** High `flexsearch_llm_tokens_total`; slow `generate` / stage latency; provider 429s in logs.

**Mitigations**

- Lower `chat.max_tokens`; disable multi-query & multihop for heavy projects.
- Watch LiteLLM / provider 429s; GraphRAG has separate retry/wait settings.
- Use `/metrics` stage histogram `flexsearch_stage_latency_seconds{stage="llm"|...}`.

---

## Quick reference — Celery (no Beat)

| Item | Value |
|---|---|
| Queues | `ingest`, `graph`, `summary`, `default` |
| Beat | None |
| Broker | Same Redis as SSE |
| Eager | Tests only (`CELERY_TASK_ALWAYS_EAGER`) |
| Crawl/bulk | `default` → create_and_enqueue → `ingest` |
| Summaries | `summary` queue; skip graph; failure keeps COMPLETED + error_message |
