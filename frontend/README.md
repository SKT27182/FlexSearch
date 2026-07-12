# FlexSearch frontend

React + TypeScript SPA for FlexSearch — the browser UI for projects, RAG configuration, document ingest (upload / crawl / bulk), Chat, and retrieval Search.

The stack is **Vite** (dev server + bundler), **React 19**, **Tailwind CSS**, **Zustand** (client state), and **React Router**. The UI talks to the FastAPI backend over `/api` (proxied in local dev).

---

## What is this UI for? (RAG mental model)

**RAG (Retrieval-Augmented Generation)** means: before an LLM answers, the system finds relevant pieces of *your* project documents and passes them as context. FlexSearch’s frontend does not run that pipeline itself — it configures it, kicks off async work, shows live progress, and presents answers/evidence.

Think of four stages; the UI maps onto them like this:

| Stage | What happens (backend) | What the UI shows / does |
|-------|------------------------|---------------------------|
| **1. Ingest / extract** | Upload, crawl, or `.ragpack` → text extraction | File drop, crawl/bulk dialogs, document status rows |
| **2. Chunk + index** | Split, embed / build graph, write indexes | Progress % / steps via SSE; graph “ready” badges |
| **3. Retrieve** | Ranked chunks or graph context for a query | **Search** tab (raw hits + scores) |
| **4. Generate** | LLM answers from retrieved context + citations | **Chat** tab (sessions, streamed tokens, citation cards) |

**Why this shape:** ingest and indexing are slow and asynchronous (Celery workers). The SPA’s job is operator UX — pick a project mode, tune RAG knobs, submit work, watch progress, then ask questions against a ready index — not to embed documents or call the LLM directly from the browser.

**Example walkthrough:** create a *vector* project → upload a PDF → watch status move `uploaded → extracting → chunking → indexing → completed` → open **Chat**, ask “What does section 3 say about X?” → read the streamed answer and click a citation to preview the source document. Switch to **Search** if you only want the ranked chunks (no answer prose) to debug retrieval quality.

```
Browser (Vite :5144)
  → /api/*  (dev proxy → backend :8889)
       Auth / Projects / Documents / Chat / Crawl / Bulk / Admin
```

1. **Auth** — Login/register store JWTs; `MainLayout` loads the user and gates protected routes.
2. **Projects list** — Create a project (pick vector / graph Neo4j / graph Microsoft) and set initial RAG config via `RagConfigForm`.
3. **Project detail** — Upload files, crawl a site, or import a `.ragpack`; watch ingest status over SSE; open **Chat** or **Search**; tweak RAG settings when needed.
4. **Admin** — Role-gated (`ADMIN` / `INFRA_ADMIN`) system and user management.

---

## Concepts and terminology

For React/Vite developers who may be new to RAG product language:

| Term | Plain meaning |
|------|----------------|
| **SPA (single-page app)** | One HTML shell; React swaps views as you navigate. No full page reload for most routes. |
| **Vite** | Dev tool that serves the app with fast HMR and builds a static bundle for production. |
| **HMR (hot module replacement)** | Edits to React files refresh in the browser without losing much UI state. |
| **Project** | A workspace: documents + a locked-in RAG mode/config + chat sessions. Everything ingest/chat/search is scoped to a project. |
| **RAG config** | Per-project knobs: how PDFs are extracted, how text is chunked, how search retrieves, chat quality stages, etc. Saved to the backend; workers and chat use it. |
| **Vector vs graph mode** | **Vector**: embed chunks and search with dense/BM25/hybrid (OpenSearch). **Graph**: entity/community graph RAG (Neo4j or Microsoft GraphRAG backend). Chosen at create (or switched later with re-index implications). |
| **Chunk** | A slice of document text small enough to embed and retrieve. Search returns chunks; Chat cites them. |
| **Embedding / dense retrieval** | Numeric vectors so “similar meaning” matches even without shared keywords. Tuned via RAG config, not in the UI math. |
| **BM25 / sparse** | Keyword-style retrieval (exact terms, IDs). Often combined with dense as **hybrid**. |
| **Chat vs Search tabs** | **Chat** asks the LLM with retrieved context (sessions, citations, streaming). **Search** is a retrieval lab: raw chunks/scores, no answer generation. |
| **SSE (server-sent events)** | One-way stream from the server (document progress, job progress, chat tokens). The UI uses `@microsoft/fetch-event-source` so Bearer JWTs can be sent — the browser’s native `EventSource` cannot set `Authorization`. |
| **Job** | Async backend work (crawl, bulk import). UI submits, gets a `job_id`, then streams progress until done. |
| **.ragpack** | Archive format for bulk import/export of project documents (and related payload the backend expects). |
| **Citation** | Pointer from a chat answer back to a source chunk/document so users can open/preview evidence. |
| **graphReady** | Gate in the UI: graph projects wait until the graph index status is ready before Chat/Search/suggestions are useful. |
| **Zustand store** | Small global React state (auth tokens/user, project list) outside component trees, with optional `localStorage` persistence. |
| **Axios interceptor** | Hook on every API call: attach JWT; on `401`, clear tokens and send the user to login. |

---

## Key product surfaces

### Project (workspace)

**What:** A project is the unit of isolation — documents, RAG mode/config, indexes, and chat sessions all belong to one project id.

**Why:** Different corpora need different extract/chunk/retrieve settings. Scoping everything to a project keeps “ask about *this* knowledge base” unambiguous and matches backend multi-tenant indexes.

**How (UI):**

- `/projects` — create with mode (`vector` / `graph_neo4j` / `graph_microsoft`) and initial `RagConfigForm` values.
- `/projects/:id` — document list, upload, crawl/bulk, settings drawer, Chat | Search tabs.
- Mode switch (when offered) updates backend RAG mode and implies re-indexing; the UI reflects graph status for graph projects.

### RAG config form

`RagConfigForm` is the UI for the project’s retrieval pipeline: extraction strategy, chunking, retrieval/rerank, hierarchical summaries (vector), graph options, and **chat quality** stages (memory, rewrite/clarify, multi-query, multi-hop, debug timings).

**What / why:** Operators tune behavior without editing YAML or restarting services. The saved JSON config is what Celery workers and the chat orchestrator read on the next ingest or question.

**How:** Form state is a `RagConfig` object (`src/lib/rag-types.ts` defaults + types). Saving calls the projects/RAG API; create-project and project-settings both reuse the same form so create and edit stay aligned.

**Example:** For a scanned-PDF corpus you might pick a stronger extract strategy and parent–child chunking; for keyword-heavy policies you might prefer hybrid retrieval. Chat quality toggles (rewrite, multi-query, …) only affect **Chat**, not the Search lab’s raw retrieve.

### Chat vs Search lab

| Concern | Chat tab (`ProjectChatPanel`) | Search tab (project detail) |
|---------|-------------------------------|-----------------------------|
| Purpose | Answer questions grounded in the KB | Inspect *what* retrieval returns |
| Generation | Yes — streamed LLM answer | No — ranked chunks / scores only |
| Sessions | Multi-session list, turns, suggestions | Stateless query form |
| Citations | Numbered sources → document preview | N/A (you already see the chunks) |
| Query stages | Honors `chat.*` config on the backend | Optional retrieval overrides (advanced) |

**Why both?** Chat hides retrieval details behind an answer. Search is the debugging lab: if Chat seems wrong, check whether the right chunks were retrieved at all (same underlying retrieve path on the backend).

**How (Chat):** List/create/delete sessions → `chatApi.stream` → status labels, tokens, citations, optional debug stage timings, suggestion/follow-up chips (`suggestionsApi`). Graph projects pass `graphReady`; until ready, chat/suggestions wait.

**How (Search):** Query + top-K (+ advanced overrides) → `retrievalApi` → list of `RetrievedChunk` with scores and text. Graph projects also wait for a ready index.

### Crawl and bulk dialogs

- **Website crawl** (`WebsiteCrawlDialog`) — Submit a start URL + `max_depth` / `max_pages`; stream job events as pages are ingested.
- **Bulk import** (`BulkImportDialog`) — Upload a `.ragpack`; stream import progress. Export downloads a pack from the same API surface.

**What / why:** Not everything arrives as a manual file drop. Crawl pulls a site into the project as documents; `.ragpack` moves a corpus between environments without re-uploading file-by-file.

**How:** POST creates a job → UI holds `job_id` → SSE/fetch stream updates progress bar and message → document list refreshes (including on `page_complete` during crawl). Long-running Celery work stays visible instead of a silent spinner.

### SSE progress (documents and jobs)

**What:** Live status without polling-only UX.

| Stream | Used for | Typical events |
|--------|----------|----------------|
| Document status (`useDocumentStatusStream` / `subscribeProjectDocuments`) | Per-file ingest pipeline | `status` / `snapshot(s)`, progress %, step, `close` |
| Job progress (`websiteApi` / `bulkApi` stream helpers) | Crawl & bulk jobs | progress, message, page/job completion |
| Chat stream (`chatApi.stream`) | Answer generation | session, status stage, citations, tokens, debug, done |

**Why fetch-event-source instead of `EventSource`:** FlexSearch APIs expect `Authorization: Bearer …`. Native `EventSource` cannot set custom headers; the Microsoft helper can.

**How (documents):** While any doc is processing, the project page subscribes; events upsert into local document state. If SSE fails, the hook can fall back to polling the list API so the UI still converges.

### Citations in the UI

**What:** Each citation is a numbered source passage (`index`, `chunk_id`, `document_id`, snippet `content`, `score`, optional `filename`) returned with the assistant turn.

**Why:** Users need to verify the answer against evidence — the core trust loop of RAG products. Without citations, Chat is just another chatbot.

**How:** During/after stream, `onCitations` fills a citation list under the answer. Clicking a citation resolves `document_id` against the project’s document list and opens `DocumentPreviewDialog` when preview is allowed. Reloading a session restores citations from stored assistant turns.

---

## Source map

| Path | Role |
|------|------|
| `src/App.tsx` | Routes: public auth + `MainLayout` shell for app pages |
| `src/layouts/main-layout.tsx` | Auth gate, sidebar, load user/projects |
| `src/pages/` | Screens: dashboard, projects, project detail, settings, admin, login/register |
| `src/components/RagConfigForm.tsx` | Editable form bound to `RagConfig` types |
| `src/components/ProjectChatPanel.tsx` | Sessions, streaming answers, citations, suggestion chips |
| `src/components/WebsiteCrawlDialog.tsx` | Start website crawl + job progress |
| `src/components/BulkImportDialog.tsx` | Import/export `.ragpack` + job progress |
| `src/lib/api.ts` | Axios client + domain APIs (`authApi`, `projectsApi`, `chatApi`, …) |
| `src/lib/rag-types.ts` | Shared TypeScript types/defaults for RAG + chat config |
| `src/stores/` | Zustand: `auth`, `project` |
| `src/hooks/useDocumentStatusStream.ts` | SSE (+ poll fallback) for document processing status |
| `src/components/ui/` | Small primitives (button, dialog, input, …) |

Path alias: `@/` → `src/` (see `vite.config.ts`).

### Routes

| Path | Page |
|------|------|
| `/login`, `/register` | Public auth |
| `/` | Dashboard |
| `/projects` | Project list / create |
| `/projects/:id` | Project detail (Chat \| Search, documents, settings) |
| `/settings` | User profile / preferences |
| `/admin` | Admin (role-gated) |

---

## API client

`src/lib/api.ts` centralizes HTTP:

- Base URL `/api`
- Request interceptor adds `Authorization: Bearer …`
- Response interceptor redirects to `/login` on `401`
- Domain helpers: auth, projects, documents, RAG config, retrieval query, chat (including stream handlers), website/bulk jobs, suggestions, admin

Chat and job streams intentionally use fetch/SSE helpers rather than plain Axios responses.

---

## Local development

```bash
# from repo root (preferred)
make install          # includes pnpm install here
cp frontend/.env.example frontend/.env
make dev-local        # API + workers + this Vite app

# or in this directory only
pnpm install
pnpm dev              # default http://localhost:5144
```

Useful scripts: `pnpm build` (typecheck + production bundle), `pnpm preview`, `pnpm lint`.

### Environment

See `.env.example`:

| Variable | Purpose |
|----------|---------|
| `VITE_PORT` | Dev server port (default `5144`) |
| `VITE_DEV_API_TARGET` | Backend origin for the `/api` proxy (default `http://localhost:8889`) |
| `VITE_APP_PUBLIC_HOST` / `VITE_ALLOWED_HOSTS` | Extra hosts Vite may serve (deploy / tunnel) |

In production the Docker image builds static assets and serves them with nginx; `/api` is expected to be reverse-proxied by the outer stack.

---

## React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Babel](https://babeljs.io/) (or [oxc](https://oxc.rs) when used in [rolldown-vite](https://vite.dev/guide/rolldown)) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

This project uses `@vitejs/plugin-react` (see `package.json` / `vite.config.ts`).

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
    },
    // other options...
  },
])
```

You can also install [eslint-plugin-react-x](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs['recommended-typescript'],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```
