# Major-upgrade acceptance checklist

- [ ] Maintenance mode is active and all task queues are drained.
- [ ] PostgreSQL, MinIO, Neo4j, OpenSearch, and GraphRAG backups restore cleanly.
- [ ] Preflight report has no ambiguous invalid RAG configurations.
- [ ] Alembic revision is exactly `009`.
- [ ] JWT signing key and operations token were rotated.
- [ ] API, worker, scheduler, and frontend use the same release identifier.
- [ ] Runtime database identity cannot mutate schema.
- [ ] Backend unit and disposable-stack integration suites pass.
- [ ] Ruff, ESLint, TypeScript build, dependency audit, secret scan, and image scan pass.
- [ ] Authorization matrix and cross-project/chat-session probes pass.
- [ ] Malicious upload, SSRF, sitemap XML, archive, and decompression probes pass.
- [ ] RAG generation switching, stale-worker fencing, and graph lease tests pass.
- [ ] Reconciliation reports no untracked objects or permanently stuck documents.
- [ ] Read, chat, ingestion, and reindex traffic were restored in that order.
