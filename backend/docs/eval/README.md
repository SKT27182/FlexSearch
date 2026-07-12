# FlexSearch evaluation

Golden-set eval harness for **retrieval@k** and a **lexical faithfulness** proxy. Designed to run in CI **without** live OpenSearch, Celery, or LLM calls.

> **Offline only.** There is no live `--project-id` / API eval path. The harness module docstring historically mentioned live credentials; that was never implemented. Do not document or rely on online eval until a real client is added.

---

## What RAG evaluation is

A RAG stack is two coupled systems:

1. **Retrieval** — given a question, surface the right passages (chunks / summaries).
2. **Generation** — given those passages, produce an answer that stays grounded in them.

Either side can look healthy while quality collapses. Retrieval can return fluent but irrelevant chunks; generation can paraphrase context into claims the passages never support. Unit tests catch crashes and schema drift. They do not catch “the pipeline still returns 200, but the top-5 no longer contains the gold chunk.”

**RAG evaluation** asks a fixed set of questions against known expectations and turns the answers into numbers:

| Layer | Question eval asks | FlexSearch measures with |
|---|---|---|
| Retrieval | Did the right ids land in the top *k*? | hit@k, recall@k, precision@k |
| Generation / groundedness | Does the answer stick to the contexts? | lexical faithfulness (token overlap) |

Industry RAG eval often adds ranking metrics (MRR, nDCG), semantic similarity, or LLM-as-judge. This harness deliberately stays smaller: set-overlap retrieval@k plus a deterministic faithfulness proxy that CI can run without models or search infra.

Treat eval as a **regression microscope**, not a substitute for production monitoring. Live empty-retrieval rates and stage timings live under [ops](../ops/README.md); this doc is the offline golden gate.

---

## Why offline eval matters

| Property | Why it matters here |
|---|---|
| **Deterministic** | Mock retrieve always returns the same id list for a case × mode. Scores move only when golden data or metric code changes. |
| **Dependency-free** | No OpenSearch, Redis, Celery, or LLM keys — `make eval` works in a cold CI job. |
| **Fast** | Seconds, not minutes of embed + search + generate. |
| **CI-gateable** | Aggregate hit@k / faithfulness can fail the build (`--min-hit-at-k`, `--min-faithfulness`). |
| **Honest scope** | Protects harness plumbing and golden contracts. Does **not** prove live ranking or LLM answer quality. |

**Offline vs online (conceptual):**

```
Online (not implemented):  question → live retrieve → live generate → score vs gold
Offline (this harness):    question → mock ids/contexts → gold_answer → score vs gold ids/texts
```

A green offline run means: “metric math and the golden set still agree with themselves under the mock path.” A red run means someone broke the harness, the thresholds, or the golden JSON — investigate before assuming production RAG regressed. Conversely, production can degrade while offline stays green if live ranking or generation drifts; that is why ops metrics and human review still matter.

---

## Why eval exists (in this repo)

RAG systems fail in two places: **retrieval** (wrong or missing chunks) and **generation** (answers that drift from retrieved context). Unit tests catch code bugs; they do not catch “the pipeline still runs but quality got worse.” Eval closes that gap with a fixed set of questions, known-good chunk ids / texts, and numeric scores that CI can gate on.

FlexSearch’s eval is intentionally **narrow and offline**: it protects metric plumbing and golden-data regressions, not live ranking or LLM answer quality. Treat a green `make eval` as “the harness and thresholds still hold,” not as “production RAG is good.”

---

## Concepts and terminology

| Term | Plain meaning |
|---|---|
| **Golden set** | A curated list of (question → expected relevant chunks / texts → optional gold answer) cases used as ground truth. Scores are only meaningful relative to this set. |
| **Retrieval@k** | Score how well the top-*k* retrieved item ids match the gold relevant ids. FlexSearch reports hit@k, recall@k, and precision@k (not MRR or nDCG). |
| **hit@k** | Did *any* relevant id appear in the top *k*? Binary per case (0 or 1), then averaged. |
| **recall@k** | Of all relevant ids, what fraction showed up in the top *k*? High recall = few misses. |
| **precision@k** | Of the items in the top-*k* list, what fraction were relevant? High precision = few distractors in the cutoff window. |
| **Faithfulness / groundedness** | Does the answer stick to the retrieved context? Here: lexical token overlap (answer tokens found in context tokens). Not semantic entailment or LLM-as-judge. |
| **Harness** | End-to-end-ish runner: load golden → mock retrieve → score → aggregate → optional CI fail. Broader than a single unit test. |
| **Unit tests** | Targeted checks of metric functions and that `run_eval` meets thresholds (`tests/test_phase5.py`). The harness is the product; pytest asserts it still works. |

### How RAG evaluation works (conceptually)

1. **Fix the questions** — golden cases with known relevant chunk ids and context texts.
2. **Run retrieval** (here: a deterministic mock that returns those ids plus distractors / summary stubs).
3. **Score retrieval** — compare retrieved id lists to `relevant_chunk_ids` at cutoff *k*.
4. **Score answer groundedness** — compare an answer string to the contexts (here: `gold_answer`, not a live LLM).
5. **Aggregate** — mean metrics overall and by hierarchy mode; fail CI if thresholds slip.

In a live eval (not implemented here), step 2 would call OpenSearch / chat and step 4 would use model output. Offline mode substitutes mocks so CI stays fast, deterministic, and dependency-free.

---

## Purpose

| Goal | What it does | What it does **not** do |
|---|---|---|
| CI gate | Fail if aggregate hit@k or faithfulness drop below thresholds | Call production retrieval or generation |
| Metric plumbing | Exercise `retrieval_at_k` + `faithfulness_score` | LLM-as-judge / semantic similarity |
| Mode coverage | Run cases under `chunks_only`, `summaries_first`, `mixed` | Measure real ranking differences between hierarchy modes |

Offline mode uses a **deterministic mock retriever**. Faithfulness compares `gold_answer` tokens to mock contexts — useful for regressions in metric code and golden data, not for end-to-end RAG quality.

---

## Architecture

```mermaid
flowchart TD
  G[golden_set.json] --> H[run_eval]
  H --> Loop[For each case × mode]
  Loop --> M[_mock_retrieve]
  M --> R[retrieval_at_k vs relevant_chunk_ids]
  M --> A[answer = gold_answer]
  A --> F[faithfulness_score vs contexts]
  R --> Case[CaseResult]
  F --> Case
  Case --> Agg[EvalReport aggregate + by_mode]
  Agg --> CLI{CLI thresholds}
  CLI -->|hit@k or faith below min| Fail[exit 1]
  CLI -->|ok| Ok[exit 0]
```

---

## Metrics (`app/eval/metrics.py`)

Retrieval metrics ask: “Given an ordered list of retrieved ids and a set of relevant ids, how good is the top *k*?” Faithfulness asks: “How much of the answer’s vocabulary is supported by the contexts?”

| Metric | Definition |
|---|---|
| **hit@k** | `1.0` if any relevant id appears in the top-k retrieved ids; else `0.0` |
| **recall@k** | \|relevant ∩ top-k\| / \|relevant\| |
| **precision@k** | \|relevant ∩ top-k\| / \|top-k list\| (length of the truncated list) |
| **faithfulness** | Fraction of answer tokens (regex `[a-z0-9]+`, lowercased) that appear in the union of context tokens |

Edge cases:

- Empty relevant set → all retrieval metrics `0.0`
- Empty answer → faithfulness `1.0`
- Answer tokens but empty contexts → faithfulness `0.0`

`mean()` averages a list of floats (empty → `0.0`).

**Not used here:** MRR (reciprocal rank of first relevant hit) and nDCG (graded relevance with discount by rank). Those matter for fine-grained ranking quality; this harness uses set-overlap style metrics plus hit@k, which are enough for CI gates on the mock path.

**Faithfulness vs groundedness:** Same idea in this codebase — the answer should be supportable from context. The implementation is a **lexical proxy** (token overlap), so paraphrases that share words score well, and answers that invent unsupported tokens score poorly. It will not catch fluent hallucinations that reuse context vocabulary incorrectly.

### Intuition: hit@k, recall@k, precision@k

Think of the top-*k* list as a short shortlist and the golden `relevant_chunk_ids` as the answer key.

| Metric | Intuition | Sensitive to |
|---|---|---|
| **hit@k** | “Did we get *at least one* useful chunk into the shortlist?” | Total misses (relevant never appears) |
| **recall@k** | “Of everything that should have been found, how much did we cover?” | Incomplete coverage when several ids are relevant |
| **precision@k** | “Of what we put in the shortlist, how much was actually useful?” | Distractors and padding inside the cutoff |

**Worked example** (illustrative; same math as `retrieval_at_k`):

```
relevant = {A, B}
retrieved (best first) = [A, X, Y, B, Z]
k = 3
top-3 = [A, X, Y]
```

| Metric | Computation | Value |
|---|---|---|
| hit@3 | A is relevant → hit | `1.0` |
| recall@3 | only A of {A,B} in top-3 → 1/2 | `0.5` |
| precision@3 | 1 relevant among 3 slots → 1/3 | `≈0.333` |

If *k* rises to `5`, top-5 includes B as well → recall becomes `1.0`, precision becomes `2/5 = 0.4`. Hit stays `1.0`. Raising *k* usually helps recall and can dilute precision — which is why the harness fixes `--k` (default `5`) when comparing runs.

**Hit vs recall:** with a single relevant id (most current golden cases), hit@k and recall@k are identical for that case (0 or 1). They diverge only when `|relevant| > 1` and the shortlist catches some but not all.

**Offline mock twist:** for `chunks_only` / `mixed`, the mock returns relevant ids first, then a `distractor:{case_id}`. At typical *k*, hit and recall stay high; precision is pulled down slightly by the distractor once *k* is large enough to include it. That is intentional — it exercises the precision formula without pretending to rank like OpenSearch.

### Intuition: lexical faithfulness

Faithfulness here is **not** “is the answer true?” It is “what fraction of the answer’s tokens also appear somewhere in the context strings?”

**Worked example** inspired by golden case `q1`:

```
context:  "FlexSearch is an enterprise RAG platform with OpenSearch retrieval and chat."
answer:   "FlexSearch is an enterprise RAG platform that uses OpenSearch for retrieval and provides chat with citations."
```

Tokens unique to the answer (roughly): `that`, `uses`, `for`, `provides`, `with`, `citations` — some overlap context (`with` may already appear; `citations` does not). Score = supported tokens / all answer tokens. A gold answer that closely paraphrases `relevant_texts` scores high; inventing a token like `kubernetes` that never appears in context lowers the score.

| Situation | Typical faithfulness | Meaning |
|---|---|---|
| Gold answer reuses context vocabulary | High (often well above `--min-faithfulness 0.5`) | Expected offline; CI stays green |
| Gold answer rewritten with new jargon | Drops | Update contexts or answer together |
| Empty answer | `1.0` by definition | Avoid accidental empty `gold_answer` masking bugs |
| Answer with tokens, empty contexts | `0.0` | Missing `relevant_texts` |

Limitations of the proxy: synonym-only rewrites can look worse than they are; copy-paste hallucinations that reuse context words can look better than they are. That is acceptable for a CI smoke gate, not for research-grade groundedness.

---

## Offline mock retriever

`_mock_retrieve(case, mode, k=…)` in `harness.py`:

| Mode | Retrieved ids | Contexts |
|---|---|---|
| `chunks_only` / `mixed` | `relevant_chunk_ids` + `distractor:{case_id}` | `relevant_texts` + distractor string |
| `summaries_first` | `summary:{case_id}` then relevant ids | Synthetic summary string + `relevant_texts` |

Lists are truncated to at least `k` for scoring. Relevant ids for hit/recall remain the **member chunk ids** even in `summaries_first` (the leading synthetic summary id does not count as a hit unless it appears in `relevant_chunk_ids`).

Answer for faithfulness:

```text
gold_answer if present else first context string
```

So offline faithfulness is usually high when gold answers paraphrase the golden texts — that is intentional for CI stability.

---

## Golden set

A **golden set** is the contract between “what we believe is correct” and the scorer. Changing ids, texts, or answers without updating expectations will move metrics — sometimes that is the point (fixing a bad case), sometimes it is accidental drift. Prefer stable `id` values so reports stay comparable over time.

### What belongs in a golden case

Each case answers three labeling questions:

1. **Ask** — what user question are we pretending to evaluate? (`question`)
2. **Retrieve** — which chunk ids *should* appear for a good retrieve? (`relevant_chunk_ids`)
3. **Ground** — what context text and reference answer should faithfulness compare? (`relevant_texts`, `gold_answer`)

Optional `modes` lists which hierarchy retrieval modes to exercise for that case (`chunks_only`, `summaries_first`, `mixed`).

**Good golden habits:**

- Stable string ids (`chunk:ssrf`, not regenerated UUIDs) so `--json` diffs stay readable.
- Relevant texts that actually support the gold answer’s tokens (otherwise faithfulness is a self-inflicted fail).
- Small, high-signal cases over a huge noisy set for CI — expand domain corpora in a separate golden file via `--golden` when experimenting.

**Bad golden habits:**

- Changing `relevant_chunk_ids` without intending a metric change.
- Putting the gold answer in jargon the contexts never use, then wondering why faithfulness collapsed.
- Treating five synthetic product facts as coverage of a customer corpus.

Path: [`backend/app/eval/data/golden_set.json`](../../app/eval/data/golden_set.json)

Schema per case:

```json
{
  "id": "q1",
  "question": "What is FlexSearch?",
  "relevant_chunk_ids": ["chunk:flexsearch-intro"],
  "relevant_texts": [
    "FlexSearch is an enterprise RAG platform with OpenSearch retrieval and chat."
  ],
  "gold_answer": "FlexSearch is an enterprise RAG platform that uses OpenSearch for retrieval and provides chat with citations.",
  "modes": ["chunks_only", "mixed"]
}
```

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Stable id for reports / trends |
| `question` | recommended | Used in mock summary text |
| `relevant_chunk_ids` | yes | Ground-truth ids for retrieval metrics |
| `relevant_texts` | yes for faith | Context strings for faithfulness |
| `gold_answer` | recommended | Answer under test for faithfulness |
| `modes` | optional | Default `["chunks_only"]` if omitted |

Current set: **5** synthetic FlexSearch product facts (queues, SSRF, metrics, hierarchical summaries). Extend with project-specific corpora; keep ids stable.

Hierarchy modes in the golden set exercise the **same metric path** used conceptually by [hierarchical summaries](../summaries/README.md) (`chunks_only` | `summaries_first` | `mixed`). They do **not** call `filters_for_hierarchy` or OpenSearch.

---

## How to run

From repo root:

```bash
make eval
```

Equivalent:

```bash
cd backend && .venv/bin/python -m app.eval \
  --k 5 \
  --min-hit-at-k 0.8 \
  --min-faithfulness 0.5
```

Useful flags:

```bash
python -m app.eval --json
python -m app.eval --modes chunks_only summaries_first
python -m app.eval --golden /path/to/custom_golden.json --json
python -m app.eval --k 10 --min-hit-at-k 0.9 --min-faithfulness 0.6
```

| Flag | Default | Meaning |
|---|---|---|
| `--golden` | `app/eval/data/golden_set.json` | Golden path |
| `--k` | `5` | Cutoff for retrieval@k |
| `--modes` | all modes in cases | Optional filter |
| `--min-hit-at-k` | `0.8` | CI fail threshold |
| `--min-faithfulness` | `0.5` | CI fail threshold |
| `--json` | off | Full `EvalReport.to_dict()` |

Exit code **`1`** if either aggregate threshold fails.

Entry points:

- `python -m app.eval` → `app/eval/__main__.py` → `harness.main`
- Library: `from app.eval import run_eval, EvalReport, retrieval_at_k, faithfulness_score`

---

## Report shape

Human output example:

```text
FlexSearch eval (k=5, n=…)
  hit@5:        0.xxx
  recall@5:     0.xxx
  precision@5:  0.xxx
  faithfulness: 0.xxx
  [chunks_only] hit=… faith=… n=…
  [mixed] hit=… faith=… n=…
```

JSON (`--json`) includes `k`, `aggregate`, `by_mode`, and per-case `CaseResult` (`id`, `mode`, metrics, `retrieved_ids`).

### Interpreting results

Aggregates are **means over case × mode rows**, not means over unique questions. A case listed under three modes contributes three rows — that is why `n` in the header is larger than “5 golden questions.”

| Signal | Likely reading (offline) | What to check |
|---|---|---|
| Aggregate hit@k ≥ `--min-hit-at-k`, faith ≥ `--min-faithfulness` | Harness + golden still coherent | Nothing urgent; still skim `--json` if you changed metric code |
| `FAIL: hit@k=… < min` | Relevant ids missing from mock top-k, or golden ids emptied / mistyped | `relevant_chunk_ids`, `_mock_retrieve`, `--k` |
| `FAIL: faithfulness=… < min` | Gold answers drifted from context vocabulary, or contexts emptied | `gold_answer` vs `relevant_texts`, tokenize edge cases |
| Precision lower than hit/recall | Expected when distractors sit inside top-k | Normal for mock; not a live ranking judgment |
| `by_mode` nearly identical | Expected offline — mock still injects member ids | Do not claim hierarchy quality wins from offline deltas |
| One case id fails in `--json`, others fine | Local golden edit or mode-specific mock path | Inspect that case’s `retrieved_ids` and texts |

**Example interpretation sketch:**

```text
hit@5:        1.000
recall@5:     1.000
precision@5:  0.850
faithfulness: 0.920
```

Reading: every case×mode shortlist contained at least one relevant id (and full coverage of relevant sets at k=5); about 15% of shortlist slots were non-relevant (distractors / summary stub); answers largely reuse context tokens. With defaults `min-hit-at-k=0.8` and `min-faithfulness=0.5`, CI passes. If faithfulness fell to `0.40` after editing `q4`’s gold answer to mention tokens absent from its `relevant_texts`, the CLI would print `FAIL: faithfulness=…` even though retrieval stayed perfect — fix the golden pair, not OpenSearch.

### Interpreting `by_mode`

In **offline** mock mode, `summaries_first` still places relevant member ids in the retrieved list after the synthetic summary id, so hit@k stays high. Do **not** treat offline `by_mode` deltas as evidence that live hierarchy retrieval is better or worse — only that each mode path in the harness ran.

---

## Harness vs pytest

| | **Eval harness** (`python -m app.eval` / `make eval`) | **Pytest** (`tests/test_phase5.py -k eval`) |
|---|---|---|
| Role | Score the golden set end-to-end and optionally fail CI on aggregates | Assert metric math and that default `run_eval` stays above thresholds |
| Scope | All cases × modes → `EvalReport` | Narrow, fast unit/integration checks |
| When to use | CI quality gate; local golden experiments (`--golden`, `--json`) | PR / developer feedback that helpers and harness still pass |

They complement each other: pytest keeps the implementation honest; the harness is the runnable quality check you threshold in CI.

```bash
cd backend
UV_NO_SYNC=1 .venv/bin/python -m pytest tests/test_phase5.py -k eval -v
```

Covers:

- `retrieval_at_k` / `faithfulness_score` unit checks
- `run_eval(k=5)` meeting default thresholds and including hierarchy modes in `by_mode`

---

## Explicit non-goals / gaps

| Gap | Status |
|---|---|
| Live project eval (`--project-id`, HTTP chat/retrieve) | **Not implemented** — offline only |
| Real OpenSearch / embedding ranking | Mock ids only |
| Generated answers vs gold | Uses `gold_answer`, not LLM output |
| LLM-as-judge faithfulness | Lexical token overlap only |
| Large domain golden sets | Five synthetic cases today |
| Wiring to Prometheus | Eval is a CLI/CI job; runtime empty-retrieval metrics live under [ops](../ops/README.md) (`MetricsRegistry`) |
| `timed_stage` helper | Exported from `app/observability/tracing.py` but **unused**; live stage timings use chat `StageTimer` → `observe_stage` |

---

## Module map

| Path | Role |
|---|---|
| `app/eval/harness.py` | `run_eval`, mock retrieve, CLI, `EvalReport` |
| `app/eval/metrics.py` | `retrieval_at_k`, `faithfulness_score`, `tokenize`, `mean` |
| `app/eval/data/golden_set.json` | Default cases |
| `app/eval/__main__.py` | `python -m app.eval` |
| `app/eval/__init__.py` | Public exports |
| `Makefile` target `eval` | CI-friendly invocation |

---

## Related docs

- [Hierarchical summaries](../summaries/README.md) — real `retrieval_mode` behavior the golden modes name-check
- [Ops](../ops/README.md) — live `empty_retrieval_rate`, `/metrics`, load smoke (complementary to offline eval)
- [Query stages](../query-stages/README.md) — chat-time rewrite / multi-query (not exercised here)
- [Chat](../chat/README.md) — production answer path
