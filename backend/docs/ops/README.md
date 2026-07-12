# FlexSearch Operations

Production ops for the enterprise RAG stack: metrics, rate limits, logging, security, worker topology, load smoke, and runbooks.

**Ops in plain terms:** keep the API healthy, know when retrieval or workers misbehave, and have a short playbook when something breaks. Observability here means three complementary signals — **metrics** (numbers over time), **logging** (what happened in a process), and **stage timing** (where latency went) — plus **health checks** for quick smoke and **runbooks** for “what do I do next?”

---

## Concepts (ops vocabulary)

| Term | Plain meaning | Why it matters |
|---|---|---|
| **Metrics** | Counters and histograms the process keeps in memory and exposes as Prometheus text | Trends and alerts (empty retrieval rate, stage p95, 429 storms) without reading every log line |
| **Tracing (here)** | Stage timers (`timed_stage`) that feed those histograms — **not** distributed OpenTelemetry traces | Shows which chat/ingest stage is slow; no cross-service span graph |
| **Logging** | Human-readable event stream (console + file), including bridged third-party libs | Root-cause detail once metrics tell you *that* something is wrong |
| **Health check** | Lightweight dependency + snapshot probe (`GET /health`) | Load balancers / operators: is OpenSearch/Redis up? Is empty-retrieval rate already bad? |
| **Rate limiting** | Cap requests per user/IP per minute on expensive routes | Protects LLM spend, crawl/bulk abuse, and shared Redis/CPU under load |
| **SSRF** | Server-side request forgery: tricking the server into fetching an internal URL | Crawl/bulk fetch on behalf of users; without URL safety, a “public” crawl could hit metadata IPs or private nets |
| **Worker topology** | Which Celery processes consume which named queues | Explains backlog, starvation (OCR ingest blocking crawl), and when to split consumers |
| **Load smoke** | Small concurrent httpx script against health/chat | Sanity check before calling something a “load test”; catches p50 blowups and 429s early |
| **Runbook** | Short incident playbook: symptoms → checks → mitigations | Reduces MTTR when the same failure modes recur |

These pieces work together: scrape **metrics** for signals, use **health** for a one-shot view, dig into **logs** / worker inspect for cause, then follow a **runbook**. Stage timing is how FlexSearch answers “where did the time go?” without a full tracing backend.

---

## Observability: metrics vs logs vs traces

Industry stacks often ship three pillars. FlexSearch implements a **pragmatic subset** of that idea — enough for operators to notice and localize RAG failures without running Jaeger/Tempo.

| Signal | Question it answers | FlexSearch shape | Example |
|---|---|---|---|
| **Metrics** | *How often / how long / how bad?* | In-process counters + histograms → `GET /metrics` (Prometheus text) | `empty_retrieval_rate` climbed from 0.05 → 0.40 after a reindex; chat volume is steady → index/content problem, not “nobody is asking” |
| **Logs** | *What exactly happened in this process?* | Unified console + file; Celery workers have their own streams | Metrics show ingest failures; worker log shows `UnsafeURLError` or OpenSearch mapping mismatch for one document |
| **Traces (classic)** | *Follow one request across services* | **Not implemented** — no OTel, no trace IDs across API ↔ worker ↔ OpenSearch | You cannot click a span from chat HTTP into the ingest task that built the chunks |
| **Stage timing (here)** | *Which named stage ate the latency?* | `timed_stage` → `flexsearch_stage_latency_seconds{stage}` | Chat p95 is high; histogram shows `generate` and `llm` buckets grew while `retrieve` stayed flat → LLM/provider, not OpenSearch |

**Mental model:** metrics are the dashboard lights; logs are the black box; stage histograms are a poor-man’s trace for “which step inside this process.” When something is wrong:

1. **Notice** via `/health` snapshot or Prometheus alert (rate, latency, 429s).
2. **Localize** with stage histograms (`rewrite` vs `retrieve` vs `generate`) or queue/worker inspect.
3. **Explain** with API or worker logs (exception text, SSRF reject, Celery discard).

Do not expect one log line per metric bump, or one distributed span per chat turn. Chat debug SSE (`chat.debug`) is a *product* latency/debug aid for a single turn — separate from scraping `/metrics`.

---

## What operators watch (with examples)

Operators do not need every series on a wall. Watch a short list that maps to user-visible failure modes; escalate with runbooks when a signal trips.

| Watch | Why | Healthy-ish example | Bad example → next step |
|---|---|---|---|
| **Empty retrieval rate** (`/health` → `metrics.empty_retrieval_rate`, or chat empty ÷ chat requests) | Users asked; we found nothing usable | Low single-digit % on a populated project | Spike after deploy/reindex → [R1](./runbooks.md) (OpenSearch health, completed docs, stage over-narrowing) |
| **Stage latency** (`flexsearch_stage_latency_seconds`) | Answers “where did the time go?” | `retrieve` and `generate` both modest | Only `llm` / `generate` balloons → provider/token path; only `ingest` / extract → OCR/worker CPU |
| **Rate-limit hits** (`flexsearch_rate_limit_hits_total`) | Abuse, misconfigured client, or limits too tight | Occasional bumps from chatty UIs | Sustained 429 storm on `chat` or `crawl` → [R5](./runbooks.md); check Redis vs memory fallback |
| **Ingest outcomes** (`flexsearch_ingest_documents_total{status}`) | Pipeline completing vs failing | Mostly `completed` | Rise in `failed` / `missing` → worker logs + document status |
| **Worker queues / backlog** (Celery inspect, stuck doc statuses) | Async path is the product for crawl/bulk/upload | Active tasks turn over; statuses advance | Docs stuck in `extracting` / crawl SSE idle → [R2](./runbooks.md); check `-Q` and concurrency |
| **Dependency health** (`GET /health` OpenSearch + Redis) | Search and broker/SSE share Redis | Both reachable | OpenSearch down → [R3](./runbooks.md); Redis down → rate limits fall back to memory, SSE/Celery degrade |
| **LLM / token counters** | Cost and provider pressure | Steady tokens per chat | Token spike with flat chat volume → stages doing N retrieves or graph rebuild LLM load |

**Worked example — “chat feels broken”:** `/health` shows Redis OK and `empty_retrieval_rate` 0.55. Scrape `/metrics`: chat requests normal, empty counter climbing, stage histograms show fast `retrieve`. That pattern points to *index content / embeddings / over-aggressive query stages*, not a dead worker. Open Search lab with the same query; if lab is also empty, fix indexing (R1), not Celery.

**Worked example — “uploads never finish”:** empty-retrieval rate is fine; documents sit in `indexing`. `celery inspect reserved` shows a long OCR task on `ingest` while `default` crawl jobs pile up on the same combined worker. Split or raise concurrency for `ingest` vs `default` (topology below + R2).

---

## Architecture (ops view)

```
Frontend → FastAPI (/api/chat, /crawl, /bulk, /jobs/.../events, /documents/.../events)
              │
              ├─ in-process metrics → GET /metrics (Prometheus text; no OTel)
              ├─ rate limits (Redis sliding window, memory fallback)
              ├─ unified logging (colored console + file; GraphRAG/LiteLLM bridged)
              ├─ ChatOrchestrator → stages → RAGPipeline → OpenSearch / Neo4j
              └─ Celery workers (ingest | graph | summary | default) ← same Redis
```

Infra (OpenSearch, Redis, Postgres, Neo4j, MinIO) is consumed from **infra-hub as-is**. FlexSearch does not run Celery Beat.

---

## Metrics & tracing

**Conceptually:** metrics answer *how often* and *how long*; FlexSearch’s “tracing” is stage-level timing into the same registry, not a distributed trace ID you can follow across services.

| Endpoint | Purpose |
|---|---|
| `GET /metrics` | Prometheus text exposition (`METRICS_ENABLED=false` → 404) |
| `GET /health` | OpenSearch + Redis smoke + JSON `metrics` snapshot |

`/health` is the operator-friendly probe: dependency reachability plus a compact metrics snapshot (including `empty_retrieval_rate`). `/metrics` is for Prometheus scrapes and richer series.

### Implementation notes

- Custom in-process registry (`app/observability/metrics.py`) — **not** `prometheus_client`, **no OpenTelemetry**.
- Counters/histograms are **process-local** (per API process; workers that import metrics have their own memory — scrape the API for chat/retrieval; ingest counters increment in the worker process that ran the task).
- Stage timing uses `timed_stage` / orchestrator hooks (`app/observability/tracing.py`) into the same registry — not distributed traces.

Because metrics are process-local, multi-replica API scrapes must be aggregated externally (Prometheus sums counters across targets). Worker-only series (e.g. ingest outcomes) live where the task ran — do not expect them on the API scrape unless that process also ran the work.

### Key series

| Metric | Meaning |
|---|---|
| `flexsearch_chat_requests_total{path,rag_mode}` | Chat query/stream volume |
| `flexsearch_chat_empty_retrieval_total` | Empty retrieval turns |
| `flexsearch_stage_latency_seconds{stage}` | Stage timings (rewrite, retrieve, generate, ingest, llm, …) |
| `flexsearch_retrieval_requests_total` / `flexsearch_retrieval_empty_total` | Pipeline retrieve calls |
| `flexsearch_llm_requests_total` / `flexsearch_llm_tokens_total{direction,source}` | LLM calls / tokens |
| `flexsearch_ingest_documents_total{status}` | Ingest outcomes (`completed`, `failed`, `missing`, …) |
| `flexsearch_rate_limit_hits_total{rule}` | 429s by rule name |

Empty-retrieval rate ≈ `chat_empty / chat_requests` (also on `/health` → `metrics.empty_retrieval_rate`). That ratio is the primary “users asked, we found nothing” signal — see runbook R1.

Stage timings are collected even when project `chat.debug` is off. Debug SSE still only emits when the project enables `chat.debug`.

### Scrape example

```yaml
# prometheus.yml fragment
scrape_configs:
  - job_name: flexsearch
    static_configs:
      - targets: ["127.0.0.1:8889"]
    metrics_path: /metrics
```

Bind `/metrics` privately or put scrape ACL in front — the endpoint is unauthenticated.

---

## Rate limits

**Conceptually:** expensive routes (chat LLM, crawl fan-out, bulk import) share Redis, CPU, and provider quotas. A sliding window says “at most N requests per identity per minute” so one client cannot monopolize the box or burn token budget. It is a **guardrail**, not auth and not a fair multi-tenant quota system.

**Why it exists (examples):**

- A buggy frontend retry loop on `/api/chat/stream` would otherwise hammer the LLM on every reconnect.
- Crawl and bulk enqueue heavy Celery work; without caps, one user can flood `default` → `ingest` and starve everyone else’s uploads.
- Suggestions / follow-up are lighter but still LLM-backed — `SENSITIVE_RULE` keeps chip endpoints from becoming a free token faucet.

Redis keeps the window **consistent across API replicas**. Without Redis, each process enforces its own in-memory window — limits look looser or uneven under multi-replica load (and `/health` will already flag Redis as down).

Env (see `backend/.env.example`):

| Variable | Default | Applies to |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `true` | Master switch |
| `RATE_LIMIT_CHAT_PER_MINUTE` | `60` | `POST /api/chat/query`, `/stream` |
| `RATE_LIMIT_CRAWL_PER_MINUTE` | `10` | `POST .../crawl` |
| `RATE_LIMIT_BULK_PER_MINUTE` | `10` | bulk import/export |
| `RATE_LIMIT_SENSITIVE_PER_MINUTE` | `30` | suggestions / follow-up |

Keyed by authenticated user id (else client IP / `X-Forwarded-For`). Redis sorted-set sliding window preferred; in-process deque if Redis is down. Exceeded → **429** + `Retry-After` (window seconds) and `flexsearch_rate_limit_hits_total{rule=...}`.

Set a limit to `0` to disable that rule while keeping others on.

Separate from API limits: GraphRAG LLM provider retries (`GRAPHRAG_RATE_LIMIT_*` in config) for Microsoft GraphRAG builds — those appear in **worker** logs during graph rebuild, not as API `Retry-After` on chat.

---

## Logging

**Conceptually:** logs are the narrative; metrics are the dashboard. Use metrics to notice a spike, then logs (API + worker) to see errors, SSRF rejects, or Celery discards. Progress for crawl/bulk/documents is also pushed over Redis SSE — that is live job state, not a substitute for durable log files.

| Piece | Behavior |
|---|---|
| `setup_unified_logging` | Colored console + file under backend logs; bridges uvicorn / SQLAlchemy / LiteLLM / GraphRAG |
| `LOG_LEVEL` | Controls verbosity (`DEBUG` also enables extra SQL/detail paths where configured) |
| Celery worker | Own process logs (`docker compose logs worker` or `logs/worker.log` under `make dev-local`) |
| Document / job progress | Not only logs — Redis SSE (see [Celery](../celery/README.md)) |

Do not expect OTel spans in logs; use `/metrics` stage histograms + worker logs for latency.

---

## Worker topology

**Conceptually:** the API accepts work and returns quickly; **Celery workers** pull named queues off Redis and do the long jobs (OCR, GraphRAG rebuild, summaries, crawl/bulk). Think of queues as **lanes**:

```
API enqueue
    │
    ├─ ingest   → extract / chunk / OpenSearch or Neo4j per-doc
    ├─ graph    → Microsoft GraphRAG project rebuild
    ├─ summary  → hierarchical summaries (vector only)
    └─ default  → website crawl + bulk import
                      └─ each page/doc → enqueue ingest
```

One shared worker (`-Q ingest,graph,summary,default`) is simple to operate: every lane shares the same process pool. Under OCR-heavy ingest, a concurrency-2 worker can spend both slots on `ingest` while crawl jobs wait on `default` — that is **queue starvation**, not a dead broker. Split consumers by `-Q` when lanes compete (see [Celery README](../celery/README.md#8-running-workers)).

There is **no Celery Beat**: nothing runs on a cron. Work appears only when the API (or another task) enqueues it. `CELERY_TASK_ALWAYS_EAGER=true` runs tasks inline in the caller — fine for tests, harmful in prod (Compose `worker` then does nothing useful).

| Deployment | Topology |
|---|---|
| Compose `worker` | **One** container, `-Q ingest,graph,summary,default`, concurrency `2` |
| `make worker-local` / `dev-local` | Same queues/concurrency on the host |
| Beat | **None** — no periodic tasks |

| Queue | Work |
|---|---|
| `ingest` | Extract / chunk / OpenSearch or Neo4j per-doc |
| `graph` | Microsoft GraphRAG project rebuild |
| `summary` | Hierarchical summaries (vector only) |
| `default` | Website crawl + bulk import → each page/doc enqueues **ingest** |

Crawl/bulk path: `default` → `create_and_enqueue_document` → `ingest`.

Known graph caveats (ops-relevant):

- Process-local `_in_flight` set does **not** coordinate across multiple worker processes (Celery task-id coalesce is the cross-process guard). Task passes `manage_in_flight=False` so the workspace does not double-acquire in-process.
- API startup runs `reconcile_interrupted_graph_indexes()`; status can call `reconcile_stale_graph_index()`.
- Mode-switch Neo4j wipe: `wipe_neo4j_graph` → `delete_project_subgraph` (aligned with `Neo4jStore`).

---

## Security checklist

### Job SSE ACL

`GET /api/jobs/{job_id}/events` requires:

1. Valid JWT
2. Resolvable job → `project_id` (Redis meta, crawl id parse, or last event)
3. `verify_project_access` (project owner)

Crawl jobs register meta at schedule time (`crawl:{project_id}:{hex}`). Bulk jobs register meta when `target_project_id` is set — always import into a project for reliable SSE.

### Crawl / bulk SSRF

**Conceptually:** SSRF (server-side request forgery) is when an attacker asks *your* server to fetch a URL they choose — often aiming at `http://169.254.169.254/` (cloud metadata), `http://localhost:...`, or a private VPC IP. Crawl and bulk import legitimately fetch remote URLs on behalf of the user; without checks, that feature is an open proxy into your network.

FlexSearch mitigates this when `CRAWL_BLOCK_PRIVATE_URLS=true` (default) via `app/services/url_safety.py`:

- Rejects private, loopback, link-local, CGNAT, cloud metadata IPs **after DNS resolve** (hostname → IPs, then block-list)
- Blocks `localhost` / `*.local`
- Crawl and bulk URL fetches do **not** blindly follow redirects; each hop is re-checked and crawl stays same-domain

**Operator view:** a user reports “crawl failed for our internal wiki” — that may be **working as intended** if the host resolves to a private range. Distinguish product SSRF policy (R4) from a broken public DNS target. Known gap: DNS TOCTOU (resolve-at-check vs resolve-at-connect); see crawler/bulk docs.

### ACL audit

| Surface | Auth | Project ownership |
|---|---|---|
| Chat query/stream | JWT | Yes |
| Crawl / bulk | JWT | Yes |
| Job SSE | JWT | Yes |
| Document SSE | JWT | Yes |
| Suggestions | JWT | Yes |
| `/metrics` | None (bind privately / scrape ACL) | — |

---

## Load smoke

**Conceptually:** a load smoke test is a cheap concurrency check — enough parallel requests to expose latency cliffs or rate-limit behavior, not a full capacity study. Use it after deploy or config changes; escalate to k6/locust when you need sustained soak or multi-scenario load.

Lightweight httpx script (not k6/locust):

```bash
# Health only
python backend/scripts/load_smoke.py --endpoint health --concurrency 8 --requests 40

# Chat (needs token + project)
export FLEXSEARCH_BASE_URL=http://127.0.0.1:8889
export FLEXSEARCH_TOKEN=...
export FLEXSEARCH_PROJECT_ID=...
python backend/scripts/load_smoke.py --endpoint chat --concurrency 4 --requests 20
```

Prints ok/error counts and latency p50 / mean / max. Interpret: rising p50 under concurrency, or 429s when rate limits are tight. For heavier suites, point k6/locust at the same endpoints.

---

## Runbooks

**Conceptually:** a runbook turns a recurring incident into a checklist so operators do not rediscover the same debugging path under pressure. Metrics and health tell you *which* runbook; the playbook lists symptoms, checks, and mitigations.

See [runbooks.md](./runbooks.md) for incident playbooks:

- Empty retrieval spike
- Celery backlog / stuck ingest
- OpenSearch down
- SSRF / crawl blocked
- Rate-limit storm (429)
- Job SSE 403 / 404
- Summary COMPLETED-with-error
- Stuck graph indexing
- LLM / token budget

---

## Related

- [Celery](../celery/README.md) — queues, task ids, SSE, no Beat
- [Eval harness](../eval/README.md)
- [Crawler](../crawler/README.md)
- [Chat](../chat/README.md)
- [Summaries](../summaries/README.md)
