# Graph RAG — Conceptual Deep Dive

A teaching document for **what Graph RAG is**, **why it exists**, and **how production systems actually retrieve answers** from a knowledge graph. This is intentionally broader than FlexSearch’s implementation reference ([README.md](./README.md)); it explains the ideas you need to design or debug an enterprise GraphRAG system.

FlexSearch specifics appear only where they clarify the concepts (Neo4j hop graph vs Microsoft community GraphRAG). For Cypher, config knobs, and file paths, use the [README](./README.md).

---

## Table of contents

1. [What Graph RAG is](#1-what-graph-rag-is)
2. [Why vector RAG is not enough](#2-why-vector-rag-is-not-enough)
3. [Core building blocks](#3-core-building-blocks)
4. [Indexing: turning documents into a graph](#4-indexing-turning-documents-into-a-graph)
5. [Schema, ontology, and unknown entities](#5-schema-ontology-and-unknown-entities)
6. [Entity resolution and graph quality](#6-entity-resolution-and-graph-quality)
7. [Embeddings in Graph RAG (chunks, nodes, communities)](#7-embeddings-in-graph-rag-chunks-nodes-communities)
8. [Vector search as the graph entry point](#8-vector-search-as-the-graph-entry-point)
9. [Why chunk embeddings still matter](#9-why-chunk-embeddings-still-matter)
10. [Local search, neighborhood expansion, and hops](#10-local-search-neighborhood-expansion-and-hops)
11. [Ranking and reranking graph context](#11-ranking-and-reranking-graph-context)
12. [Community detection (Leiden) and global search](#12-community-detection-leiden-and-global-search)
13. [Choosing a retrieval strategy](#13-choosing-a-retrieval-strategy)
14. [Hybrid enterprise pipelines](#14-hybrid-enterprise-pipelines)
15. [Answer generation and citations](#15-answer-generation-and-citations)
16. [Evaluation](#16-evaluation)
17. [Cost, ops, and failure modes](#17-cost-ops-and-failure-modes)
18. [Related approaches (beyond “classic” GraphRAG)](#18-related-approaches-beyond-classic-graphrag)
19. [Mental model cheat sheet](#19-mental-model-cheat-sheet)
20. [Where FlexSearch fits](#20-where-flexsearch-fits)

---

## 1. What Graph RAG is

**Graph RAG** (Graph Retrieval-Augmented Generation) is a family of systems that:

1. **Index** documents into a **knowledge graph** (entities + relationships), often with links back to source text.
2. **Retrieve** at query time by walking that graph (and/or reading precomputed community summaries), not only by embedding similarity.
3. **Generate** an answer with an LLM, grounded in the retrieved graph context and passages.

It is still RAG: the model does not “know” your corpus by default. What changes is the **retrieval substrate**.

| Layer | Classic vector RAG | Graph RAG |
|---|---|---|
| Index | Chunk embeddings (± sparse/BM25) | Entities, edges, passages/text units, optionally communities |
| Retrieval cue | “Text similar to the query” | “Things connected to seed entities” + optional semantic entry |
| Strength | Topical paraphrase match | Multi-hop links, entity-centric dossiers, corpus themes |
| Weakness | Weak at explicit relational chains | Extraction noise, ontology drift, higher index cost |

**One-line intuition**

> Embeddings answer *“what text sounds like this?”*  
> Graphs answer *“what is connected to what?”*  
> Graph RAG uses both so the system can *find* the right neighborhood and then *reason along links*.

---

## 2. Why vector RAG is not enough

Vector search is excellent when the answer lives in one or a few passages that *sound like* the question:

- “What does the policy say about remote work?”
- “Summarize the introduction of the Q3 report.”
- “Where is force majeure defined?”

It struggles when the answer requires **stitching facts across documents** or **following a chain of named things**:

| Question | Why vectors struggle | What a graph adds |
|---|---|---|
| “How are Acme and Gamma related?” | Doc A: Acme acquired Beta. Doc B: Beta’s CFO joined Gamma. No single chunk contains the full path. | Hop Acme → Beta → Gamma; pull passages along the path |
| “Who leads the company that owns GitHub?” | Query may not contain “Satya” or even “Microsoft.” | Seed GitHub → OWNS → Microsoft → CEO → Satya |
| “What are the major AI themes across all filings?” | Top-k chunks are local; they do not summarize the corpus. | Community reports / thematic clusters |

Classic failure mode of vector-only RAG on relational questions: the retriever returns *individually relevant* passages that do not form a coherent path, and the LLM invents a connection or refuses.

---

## 3. Core building blocks

### 3.1 Knowledge graph (plain language)

A knowledge graph is a network of **nodes** (things) and **edges** (connections).

| Term | Meaning | Example |
|---|---|---|
| **Entity / node** | A named thing extracted from text | `Microsoft` (Organization), `GitHub` (Product) |
| **Relationship / edge** | A typed link between entities | `(Microsoft)-[:OWNS]->(GitHub)` |
| **Properties** | Key/value attributes on nodes or edges | `description`, `weight`, `source_chunk_ids` |
| **Passage / text unit / chunk** | Source text that mentions entities | “Microsoft acquired GitHub in 2018.” |
| **Mention** | Link from text → entity | Passage P12 `MENTIONS` GitHub |
| **Community** | Densely connected cluster of entities | “Microsoft developer ecosystem” cluster |
| **Community report** | LLM summary of a community’s theme | Used for global / thematic questions |

**Important:** the graph is not the citation. **Passages (or text units) are the evidence layer.** Entities and edges organize retrieval; answers should still quote or cite source wording.

### 3.2 Two complementary indexes

Most production GraphRAG systems maintain (at least) two retrieval surfaces:

```
Documents
   │
   ├─► Chunk / passage store     (vector ± fulltext)   ← semantic entry
   │
   └─► Knowledge graph           (entities + edges)    ← relational structure
            │
            └─► (optional) communities + reports       ← thematic map
```

FlexSearch’s Neo4j backend stores passages *inside* Neo4j and uses entity embeddings + fulltext as the seed finder. Microsoft GraphRAG stores text units and community reports in a workspace (parquet/GraphML). Same idea, different packaging.

### 3.3 Local vs global (conceptual)

| Mode | Starts from | Retrieves | Best for |
|---|---|---|---|
| **Local** | Entities named or implied by the question | Neighborhood (neighbors, paths, mentioning passages) | “Who owns X?”, “How are A and B related?” |
| **Global** | Corpus structure (communities / reports) | Theme summaries, sometimes map-reduce across communities | “What are the main themes?”, “Overview of risk topics” |

“Global” does **not** always mean the same algorithm. In Microsoft GraphRAG it means community reports. In some systems (including FlexSearch Neo4j) a similarly named “global” path may be passage fulltext — useful, but not thematic community search. Always ask: *what artifact am I reading?*

---

## 4. Indexing: turning documents into a graph

Indexing answers: *what things appear in these documents, and how are they linked?* It does not answer the user yet.

### 4.1 Typical pipeline

```
Documents
  → extract text (OCR / PDF / HTML / …)
  → split into passages / text units
  → LLM (or NLP) extract entities + relationships per unit
  → resolve / merge duplicate entities
  → write graph (nodes, edges, mention links)
  → embed chunks and/or node descriptions
  → (optional) run community detection + write community reports
```

### 4.2 Passage splitting

Passages are windows of source text. Design tradeoffs:

| Choice | Effect |
|---|---|
| Smaller passages | Cleaner extraction, weaker cross-sentence relations |
| Larger passages | More intra-passage relations, noisier / costlier LLM calls |
| Overlap | Reduces boundary cuts; can duplicate mentions |

Relations extracted from one passage usually only connect entities **seen in that same passage**. Cross-document paths emerge later because **shared entity nodes** stitch passages together.

### 4.3 LLM extraction shape

A common JSON contract:

```json
{
  "entities": [
    {"name": "Microsoft", "type": "Organization", "description": "Technology company that acquired GitHub"}
  ],
  "relationships": [
    {
      "source": "Microsoft",
      "target": "GitHub",
      "type": "acquired",
      "description": "Microsoft acquired GitHub in 2018"
    }
  ]
}
```

Good descriptions matter: they become the text you embed for **node vectors**, and they help the answer LLM when passage text is thin.

### 4.4 What gets stored on a node (beyond the name)

Enterprise graphs rarely store only `name="Microsoft"`. Typical properties:

- `type` / ontology class
- `description` (LLM-written or aggregated)
- `aliases` (`MS`, `Microsoft Corporation`)
- `source_chunk_ids` / mention edges back to passages
- `embedding` of a rich node string
- confidence / provenance / first-seen document
- optional temporal fields (`valid_from`, `valid_to`)

### 4.5 Edge weights and provenance

Not all edges are equal:

- Co-occurrence (“mentioned with”) is weaker than explicit “acquired” / “employs”
- Extraction confidence can become an edge weight
- Multiple passages supporting the same relation can strengthen weight
- Provenance (which documents assert the edge) enables audit and conflict detection

---

## 5. Schema, ontology, and unknown entities

### 5.1 Why a schema exists

Without a schema, extractors invent unbounded types (`Thing`, `Concept`, `Stuff`, `AI thing`). The graph becomes hard to query, hard to govern, and hard to visualize.

A simple enterprise ontology might allow:

```
Person | Organization | Product | Technology | Location | Event
```

plus a fixed (or curated) set of relationship types: `employs`, `owns`, `located_in`, `partners_with`, …

### 5.2 What if something new is not in the schema?

Suppose the document says:

> GPT-5 was trained using Reinforcement Fine-Tuning.

The extractor proposes entity **Reinforcement Fine-Tuning**, but the ontology has no `TrainingTechnique` type.

**Option A — Strict reject**

Ignore entities/relations that do not fit the schema.

- Pros: regulated domains (banking, healthcare), clean graph
- Cons: information loss

**Option B — Map to closest type**

LLM (or rules) maps unknown → nearest allowed type (`Technology`).

- Pros: keeps coverage
- Cons: semantic dilution; “technique” and “product” collide in one bucket

**Option C — Catch-all `Other`**

Unknown types land in `Other`.

- Pros: no silent drop; analysts can mine frequent `Other` values
- Cons: `Other` becomes a junk drawer unless reviewed

**Option D — Schema evolution (most enterprise systems)**

After enough volume you notice:

```
Reinforcement Fine-Tuning, Preference Optimization, RLHF, DPO, ORPO
```

appearing thousands of times. Ontology owners add:

```
TrainingTechnique
```

Future docs classify correctly; historical nodes can be migrated in a batch job.

**Enterprise reality:** ontologies are living assets. Version them, review extraction drift, and treat schema changes as data migrations — not one-line config edits.

### 5.3 Open vs closed information extraction

| Style | Behavior | Use when |
|---|---|---|
| **Closed IE** | Only allowed types/relations | High precision, compliance |
| **Open IE** | Free-form predicates and types | Exploration, research corpora |
| **Hybrid** | Closed core + `Other` / open tail | Most production GraphRAG |

---

## 6. Entity resolution and graph quality

Graph RAG quality is often limited more by **messy entities** than by hop algorithms.

### 6.1 The merge problem

If “Microsoft”, “microsoft”, and “Microsoft Corp” become three nodes, multi-hop paths break.

Common strategies:

| Strategy | Idea | Risk |
|---|---|---|
| Normalize name (lowercase/strip) | Cheap merge key | Collisions (“Apple” fruit vs company) |
| Alias tables | Curated synonyms | Manual cost |
| Embedding near-duplicate clustering | Soft merge candidates | False merges |
| Type-aware keys | `(name, type)` | Misses cross-type aliases |
| LLM / human review queue | High precision | Expensive |

FlexSearch Neo4j merges by **lowercased entity name within a project** — simple and effective for many corpora, with the usual collision caveat.

### 6.2 Relation quality issues

- Hallucinated edges (LLM invents a link not in the passage)
- Direction errors (`A acquired B` stored backwards)
- Self-loops and trivial co-mentions
- Over-extraction (everything related to everything)

Mitigations: constrain JSON schema, require evidence spans, drop low-confidence edges, cap entities per passage, and periodically sample-audit the graph.

### 6.3 Graph hygiene jobs (beyond the chat)

Production systems often run offline jobs:

- Deduplicate nodes
- Collapse duplicate edges
- Recompute centrality / PageRank
- Refresh community partitions
- Rebuild stale node descriptions from latest mentions
- Detect ontology drift (new frequent types in `Other`)

---

## 7. Embeddings in Graph RAG (chunks, nodes, communities)

A common confusion: **whose embedding is stored for a node?**

There are usually **multiple** embedding collections.

### 7.1 Chunk / passage embeddings

Embed the full text unit:

```
"Microsoft acquired GitHub in 2018."
```

Stored in a vector index (OpenSearch k-NN, Pinecone, Neo4j vector index, FAISS, …).

**Purpose:** semantic entry when the query does not name the right entity.

### 7.2 Node embeddings

Usually **not** an embedding of the bare string `"Microsoft"`.

Richer node text produces better vectors, for example:

```
Microsoft
Type: Organization
Description: Technology company. Owner of GitHub.
Developer of Windows. Creator of Azure.
Aliases: MS, Microsoft Corporation
```

**Purpose:** find seed entities directly from a natural-language query; also useful for ranking neighbors by semantic fit.

### 7.3 Community / report embeddings (optional)

Embed community report titles or summaries so thematic questions can ANN-search the “map of the corpus.”

### 7.4 Same ANN machinery as classic RAG

Chunk (and node) search typically uses the same approximate nearest neighbor algorithms as vector RAG:

- **HNSW** (very common)
- IVF / IVF-PQ
- DiskANN, ScaNN
- Backed by FAISS, Milvus, Qdrant, Weaviate, Pinecone, OpenSearch k-NN, Neo4j vector indexes, etc.

GraphRAG does **not** invent a new vector search algorithm. It uses ANN to **enter** the graph, then graph structure to **expand**.

---

## 8. Vector search as the graph entry point

### 8.1 The problem: queries without explicit entities

User asks:

> Which company purchased the popular code hosting platform?

The query never says **GitHub**. Pure graph lookup has nothing to seed on.

### 8.2 The solution: embed → nearest chunk → extract entity → traverse

```
Query
  → Embedding(query)
  → ANN over chunk embeddings
  → Top chunks (e.g. “GitHub is a software development platform”)
  → Extract entity: GitHub
  → Find GitHub node
  → Traverse graph (owners, related products, …)
```

Walkthrough:

| Chunk | Why it matches or not |
|---|---|
| “Microsoft acquired GitHub in 2018.” | Good, but may rank below a definitional chunk |
| “GitHub is a software development platform.” | “code hosting platform” ≈ “software development platform” |
| “Apple released Vision Pro.” | Far from query semantics |

Vector search returns the definitional chunk → extract **GitHub** → graph traversal can answer “who purchased it?” via `OWNS` / `acquired` edges (and mentioning passages).

### 8.3 Alternative: search node embeddings directly

```
Query → ANN over node description vectors → Microsoft / GitHub seeds → traverse
```

This can skip chunk retrieval. Tradeoff: node descriptions are shorter than chunks, so **semantic recall is often lower**. Many systems search **both** in parallel and merge candidates.

### 8.4 Fulltext / BM25 as a third entry

Lexical search still helps for rare proper nouns, IDs, and exact phrases that embeddings blur. Hybrid seed finding (vector + fulltext) is standard in enterprise retrieval.

---

## 9. Why chunk embeddings still matter

Suppose a node stores:

```
Microsoft
source_chunks: [chunk1, chunk4, chunk18]
```

That mapping is only useful **after** you already found the Microsoft node.

| Query | Need chunk vectors? |
|---|---|
| “Who owns GitHub?” | Often no — extract GitHub, go to node |
| “Which company bought the largest software repository?” | Yes — no node named “largest software repository” |
| “Which company acquired the platform used by developers worldwide?” | Yes — semantic bridge to GitHub, then hop to owner |

**Division of labor**

- Graph: *what is connected to what?*
- Embeddings: *what text (or node description) is semantically similar to the query?*

Removing chunk (or node) vectors collapses GraphRAG into “only works when the user names entities correctly.”

---

## 10. Local search, neighborhood expansion, and hops

### 10.1 Seed → expand → collect evidence

Local search in diagram form:

```
Query
  → find seed entities (NER + vector + fulltext)
  → expand neighborhood up to max_hops
  → collect related entities, edges, mentioning passages
  → rank / truncate to context budget
  → LLM answers
```

### 10.2 Does neighborhood expansion mean “all immediate neighbors”?

Yes in the naive sense — but production systems almost always:

1. Limit **radius** (`max_hops`, e.g. 1–3)
2. Limit **fan-out** (top-N neighbors by score)
3. Prefer certain edge types
4. Cap total tokens before the LLM

Example graph:

```
          GitHub
             |
Azure --- Microsoft --- Windows
             |
          LinkedIn
```

- **1-hop from Microsoft:** GitHub, Azure, Windows, LinkedIn  
- **2-hop:** also neighbors of those nodes (can explode quickly)

Without ranking, large neighborhoods drown the LLM context window.

### 10.3 If max hops = 3 and the answer appears at hop 2, does it stop?

The graph itself does not “know” the answer. **Retrieval policy** decides:

| Policy | Behavior |
|---|---|
| **Budget-only** | Always explore up to `max_hops`, then rank |
| **Intent-satisfied early stop** | Stop when a path supports the question (e.g. CEO found) |
| **Beam / guided expansion** | Expand only promising edges (LLM or scorer chooses next hop) |
| **Multi-path gather** | Continue to hop limit to collect alternate evidence, then rank |

Example path:

```
GitHub → Microsoft → Satya
```

Question: “Who is the CEO of the company that owns GitHub?”

At hop 2, Satya is available. A smart retriever may stop; a conservative one may still gather more context (board members, org chart) and let the reranker trim.

### 10.4 Multi-hop reasoning vs multi-hop retrieval

Do not confuse:

- **Multi-hop retrieval:** walk edges in the graph / fetch more chunks.
- **Multi-hop reasoning:** the LLM (or a planner) chains intermediate conclusions.

You can have either without the other. Graph RAG’s advantage is making multi-hop *retrieval* explicit and auditable as paths.

---

## 11. Ranking and reranking graph context

After expansion you may have dozens of nodes and passages. Sending all of them to the LLM is usually worse than sending the best ten.

### 11.1 Signals commonly combined

Suppose the query is: *“Tell me about Microsoft’s developer ecosystem.”*  
1-hop candidates: GitHub, Azure, Windows, LinkedIn.

| Signal | Idea | Example |
|---|---|---|
| **Graph distance** | Closer hops score higher | 1 hop = 1.0, 2 hops = 0.7, 3 hops = 0.4 |
| **Edge weight / type** | Strong relations beat weak co-mentions | `OWNS` ≫ `MENTIONED_WITH` |
| **Semantic similarity** | Node/passage embedding vs query | GitHub ≫ LinkedIn for “developer ecosystem” |
| **Centrality** | Important hubs get a boost | PageRank, degree, betweenness |
| **Recency / provenance** | Prefer fresh or authoritative sources | Policy docs over forum scrapes |
| **Diversity** | Avoid near-duplicate passages | MMR-style penalties |

A typical combined score is a weighted sum (or learned ranker) over these features.

### 11.2 Cross-encoder rerankers

Bi-encoder ANN compares vectors independently (fast, approximate). A **cross-encoder reranker** reads query and candidate **together** and scores relevance more accurately (slower, usually on top-100 → top-10).

Important: you do **not** rerank the bare string `"GitHub"`. You rerank a **rich representation**, for example:

```
Node: GitHub
Type: Organization / Product
Description: Software development platform; version control; 100M developers
Relations: Owned by Microsoft; connected to VS Code, Copilot, Actions
```

or even a small **subgraph serialization**:

```
GitHub -[:OWNED_BY]-> Microsoft
GitHub -[:RELATED]-> VS Code
GitHub -[:RELATED]-> Copilot
```

### 11.3 What gets ranked — nodes, passages, or both?

Enterprise pipelines often:

1. ANN retrieve top ~100 chunk/node/community candidates  
2. Graph-expand around seeds  
3. Build candidate **contexts** (passage text, node cards, path strings, community blurbs)  
4. Cross-encoder rerank contexts  
5. Pack top ~5–15 into the LLM prompt  

Passages usually win for citations; node cards and paths help the model see structure.

---

## 12. Community detection (Leiden) and global search

### 12.1 Why communities exist

Local search answers entity-centric questions. Global / thematic questions need a **map of the whole graph**:

> What are the major AI themes across all reports?

You cannot hop from a single seed to answer that well. Community detection clusters densely connected regions; an LLM then writes a **community report** summarizing each cluster.

### 12.2 Leiden in plain language

Start with a toy graph:

```
Microsoft — GitHub — VS Code
    |
  Azure

Google — Gemini — Android
```

Initially every node is its own community. Leiden repeatedly asks:

> If I move this node into a neighboring community, does the graph become more modular (denser inside, sparser between)?

Moves that increase **modularity** are accepted. Eventually:

| Community 1 | Community 2 |
|---|---|
| Microsoft, GitHub, VS Code, Azure | Google, Gemini, Android |

Leiden is preferred over older Louvain-style methods in many GraphRAG stacks because it avoids some poorly connected communities and scales well.

### 12.3 Hierarchical communities

Large corpora often build a **hierarchy**:

- Level 0: fine-grained clusters  
- Level 1–N: merged super-communities  

Retrieval can pick a `community_level` (detail vs overview). Dynamic community selection may choose which reports to read based on the query.

### 12.4 Global search pattern (Microsoft-style)

```
Query
  → select relevant community reports (embedding / map step)
  → map: partial answers per report
  → reduce: synthesize final answer
```

### 12.5 Communities also help local search

Community summaries are not only for global questions. For a local ask about Microsoft, including the community report for Microsoft’s cluster gives the LLM **high-level context** alongside detailed passages and paths.

### 12.6 What if your backend has no communities?

Then “global” must mean something else (e.g. corpus-wide passage fulltext, or vector search over all chunks). That is still useful — but it is **not** thematic community GraphRAG. FlexSearch’s Neo4j path is hop-local; community reports exist on the Microsoft GraphRAG backend.

---

## 13. Choosing a retrieval strategy

Strategy selection is an active design area. A practical production approach:

### 13.1 Query analysis

Ask (rules, classifier, or LLM):

1. Does the query mention specific entities?
2. Is it about one entity or the whole corpus?
3. Does it need chained relationships (multi-hop)?
4. Are there zero recognizable entities?

### 13.2 Strategy map

| Query pattern | Prefer |
|---|---|
| “Who is Microsoft’s CEO?” | Local graph search |
| “What are major AI themes across all reports?” | Global / community search |
| “Who leads the company that owns GitHub?” | Multi-hop local traversal |
| “Which company bought the largest code hosting platform?” | Vector entry → entity seed → graph expand |
| “Find the paragraph defining force majeure” | Classic vector / hybrid RAG (graph optional) |

### 13.3 Do not pick only one

Robust systems run several paths in parallel:

```
User Query
   ├── Entity extraction
   ├── Chunk vector search (ANN)
   ├── Node vector / fulltext search
   ├── Graph traversal from seeds
   └── Community report retrieval
            │
            ▼
     Merge + rerank
            │
            ▼
           LLM
```

Different methods cover different failure modes. Vector rescue helps when NER misses; graph hops help when similarity alone cannot chain facts; communities help when the question is thematic.

---

## 14. Hybrid enterprise pipelines

### 14.1 End-to-end sketch

```
User Query
   │
   ▼
Embedding
   │
   ▼
ANN (HNSW) → Top 100 candidates
   │
   ├── Chunk candidates
   ├── Node candidates
   └── Community candidates
   │
   ▼
Graph expansion (bounded hops / edge filters)
   │
   ▼
Feature scoring + cross-encoder rerank
   │
   ▼
Top 10 contexts (passages + paths + optional reports)
   │
   ▼
LLM answer (+ citations)
```

### 14.2 Why hybrid beats pure graph or pure vector

| Failure | Compensated by |
|---|---|
| Query has no entity names | Chunk/node ANN |
| ANN returns related but unlinked text | Graph hops / paths |
| Local neighborhood is too narrow for themes | Community reports |
| Neighborhood is huge | Distance + semantic + reranker |
| Entity name collision | Type filters, aliases, human ontology |

### 14.3 Agent / planner variants

Some systems let an LLM **plan** tools:

1. `search_chunks(query)`
2. `get_entity(name)`
3. `expand_neighbors(entity_id, hops=2)`
4. `read_community(community_id)`

This is flexible but harder to evaluate and control than a fixed hybrid DAG. Enterprise deployments often start fixed, then add limited agentic hops for hard queries.

---

## 15. Answer generation and citations

### 15.1 What to put in the prompt

High-quality GraphRAG prompts usually include:

- Ranked **passage text** (primary evidence)
- Compact **entity cards** (name, type, description)
- **Relationship / path** lines for multi-hop asks
- Optional **community summary** for regional context
- Clear citation markers (`[P12]`, document ids, filenames)

### 15.2 Grounding rules

Instruct the model to:

- Prefer passage wording over inventing facts from entity names alone
- Say when the graph path is incomplete
- Distinguish “connected in the graph” from “causally true in the world”

### 15.3 Path verbalization

For multi-hop questions, serializing the path helps:

```
GitHub -[:OWNED_BY]-> Microsoft -[:HAS_CEO]-> Satya Nadella
```

plus the passages that support each edge. This reduces hallucinated shortcuts.

---

## 16. Evaluation

Graph RAG needs metrics beyond “does the answer sound good?”

### 16.1 Retrieval metrics

| Metric | What it checks |
|---|---|
| Context recall / precision | Did we fetch the right passages? |
| Seed entity hit rate | Did we find the correct starting nodes? |
| Path existence / hop sufficiency | Is `max_hops` enough for gold paths? |
| Neighborhood precision | After expansion, how much noise remains? |

### 16.2 Generation metrics

| Metric | What it checks |
|---|---|
| Answer correctness (LLM-as-judge or human) | Final response quality |
| Citation faithfulness | Claims supported by retrieved text |
| Multi-hop accuracy | Correct only if chain is used, not guessed |

### 16.3 Graph quality metrics

| Metric | What it checks |
|---|---|
| Entity resolution accuracy | Merge / split errors |
| Relation precision/recall vs labeled extracts | Extraction quality |
| Ontology coverage | % of entities forced into `Other` |
| Community coherence | Human rating of reports |

### 16.4 Golden sets

Build questions in buckets:

1. Single-entity lookup  
2. Explicit multi-hop  
3. Implicit entity (needs vector entry)  
4. Thematic / global  
5. Adversarial (ambiguous names, missing facts)

Measure each bucket separately — overall averages hide GraphRAG’s strengths and weaknesses.

---

## 17. Cost, ops, and failure modes

### 17.1 Cost drivers

| Stage | Why it is expensive |
|---|---|
| Per-passage LLM extraction | Dominant indexing cost |
| Community report generation | Extra LLM pass over clusters |
| Query-time LLM planners / multi-query | Latency + $ |
| Cross-encoder rerank | GPU/CPU on every query |
| Full corpus rebuilds | Microsoft-style project indexes |

Incremental per-document graphs (Neo4j-style) amortize cost as files arrive. Project-level rebuilds batch work but delay readiness and can be costly to refresh.

### 17.2 Latency budget

Typical query latency sources:

1. Embedding encode  
2. ANN search  
3. Graph expansion (DB round-trips)  
4. Rerank  
5. LLM generation  

Cap expansion and always enforce a context token budget.

### 17.3 Common failure modes

| Failure | Symptom | Mitigation |
|---|---|---|
| Missed seed entity | Empty or wrong neighborhood | Hybrid ANN + fulltext + better NER |
| Entity collision | Nonsense merges | Type-aware IDs, aliases, review |
| Hop explosion | Noisy context, worse answers | Rank, filter edge types, lower hops |
| Under-hopping | Missing bridge entities | Raise `max_hops` or guided expansion |
| Extraction hallucination | Fake edges | Evidence spans, confidence filters |
| Stale graph | Answers miss new docs | Incremental index + rebuild SOP |
| Global/local mixup | Thematic ask on hop-only backend | Route to community-capable index |

### 17.4 Security and tenancy

Enterprise graphs must scope by tenant/project on **every** query (entity search, hop expand, passage fetch). A missing `project_id` filter is a data-leak class bug. Also scrub secrets from extracted descriptions before they are embedded or shown.

---

## 18. Related approaches (beyond “classic” GraphRAG)

The term “GraphRAG” is overloaded. Related ideas:

| Approach | Core idea |
|---|---|
| **Microsoft GraphRAG** | Entity graph + Leiden communities + community reports; local/global search |
| **Property-graph RAG (Neo4j-style)** | Live KG + hop expansion + passage mentions; strong for incremental entity questions |
| **LightRAG** | Lighter graph indexing / dual-level retrieval emphasis |
| **HippoRAG / PathRAG-style** | Personalized PageRank / path-centric retrieval over extracted KG |
| **KG-augmented RAG** | Use an existing curated ontology (not only LLM-extracted) |
| **Agentic graph tools** | LLM calls graph query tools iteratively |

When reading papers or vendor blogs, identify which artifacts they build (edges only? communities? reports?) and which query path they optimize (local hops vs global themes).

---

## 19. Mental model cheat sheet

```
                    ┌─────────────────────────┐
                    │     User question       │
                    └───────────┬─────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
        Vector/ANN         Entity match      Community search
        (chunks/nodes)     (name/fulltext)   (reports)
              │                 │                 │
              └────────┬────────┘                 │
                       ▼                          │
                 Seed entities                     │
                       │                          │
                       ▼                          │
              Bounded hop expansion               │
                       │                          │
                       ▼                          │
         Passages + paths + node cards ◄──────────┘
                       │
                       ▼
              Rank / rerank / truncate
                       │
                       ▼
                   LLM answer
```

**Remember**

1. Schema decisions determine what can be stored and queried later.  
2. Node embeddings are rich descriptions, not bare names.  
3. Chunk vectors exist to *find seeds* when the user does not name them.  
4. Neighborhood expansion without ranking will overflow context.  
5. Leiden communities power thematic global search — they are not required for every GraphRAG.  
6. Hybrid retrieval + reranking is the production default, not a luxury.

---

## 20. Where FlexSearch fits

FlexSearch `rag_mode=graph` exposes two backends behind the same product surface (`graph_local` / `graph_global`):

| Backend | Conceptual role |
|---|---|
| **Neo4j** | Incremental property graph: passages, entities, `MENTIONS`, `RELATES_TO` hops; entity vector/fulltext seeding; **no Leiden communities** |
| **Microsoft GraphRAG** | Project-level workspace with communities + community reports; true thematic `global_search` |

Use this deep dive for *ideas and design decisions*. Use [README.md](./README.md) for *how FlexSearch implements them* (pipelines, Cypher, config, gaps).

---

## Related reading in this repo

- Implementation reference: [README.md](./README.md)
- Chat / query stages (rewrite, multi-query, etc.): [`../chat/README.md`](../chat/README.md), [`../query-stages/README.md`](../query-stages/README.md)
- RAG module overview: [`../../app/rag/README.md`](../../app/rag/README.md)
