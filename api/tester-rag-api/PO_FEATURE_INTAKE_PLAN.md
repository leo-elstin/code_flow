# PO Feature Intake — Product & Architecture Plan

**Feature:** User types `"Integrate a <X> feature"` → an AI Product Owner agent deep-researches (codebase + web), drafts a PRD, and decomposes it into epics, stories, and tickets in the existing backlog — each story sized to be executable by the existing code agent.

Status: PLAN ONLY — no code changes. Author: PO/Architect session, 2026-06-11.

---

## 1. Is this really required? (PO verdict)

**Verdict: Yes — build it, but as a thin intake layer on top of existing primitives, not a standalone research product.**

### The gap it closes

Today's flow forces the user to be the Product Owner:

```
User hand-writes a ticket (title + description)
  → starts a run → Planner makes ONE implementation plan → approve → dev → verify → QA
```

Three concrete problems in the current product:

1. **One request = one run.** A real feature ("Integrate payments") exceeds what one run can safely do — `CODE_AGENT_MAX_VERIFIER_ITERATIONS=3`, dev agent is told to minimize scope, QA reviews one diff. Large requests either fail verification loops or produce sprawling diffs. Nothing decomposes a big request into run-sized slices.
2. **Tickets are the weakest artifact in the system.** `CreateTicketRequest` is just `title / description / ticket_type`. No acceptance criteria, priority, sizing, ordering, or grouping — yet the entire downstream pipeline quality depends on ticket quality. (Phase-1 item B1 already had to remove *fabricated* criteria from the UI because real ones don't exist at ticket level.)
3. **The Planner already does discovery, but per-run and too late.** `feature_discovery.discover_context()` (grep → tree-sitter → LSP) runs after the user has already framed the work. Product-level decisions ("which package?", "what's out of scope?", "what order?") have no home.

### Why it fits this app specifically

This app *is* a backlog-driven autonomous dev tool: Projects → Tickets → Runs → Approval → Diffs. The missing front door is exactly "turn an intent into a runnable backlog." The market confirms the pattern: Jira AI creates user stories and acceptance criteria from prompts and turns Confluence/Slack threads into issues; ChatPRD turns notes into structured PRDs; AI agents now go "Jira ticket → pull request" — which is precisely this app's back half. We have the back half; this builds the front half.

Sources: [Atlassian Jira AI guide](https://www.atlassian.com/software/jira/service-management/product-guide/tips-and-tricks/artificial-intelligence), [ChatPRD integrations](https://www.chatprd.ai/resources/ai-agents-product-management-integrations-tools), [eesel — Jira AI ticket creation 2026](https://www.eesel.ai/blog/jira-ai-ticket-creation), [Bitmovin — Jira ticket to PR](https://bitmovin.com/blog/ai-developer-workflows-jira-to-pull-request/)

### Honest counterarguments (and how the plan answers them)

| Risk | Mitigation in this plan |
|---|---|
| Scope creep into "yet another PRD tool" | PRD is a means, not the product. Output contract = runnable tickets in the existing store; PRD is a one-screen brief. |
| Duplicates Planner discovery | PO agent *reuses* `discover_context()` and RAG `/api/query`; it adds decomposition + web research, not a second discovery engine. |
| Web research cost/latency | Hard caps (queries, fetches, tokens), per-intake cache, config flag to degrade to codebase-only. See §6. |
| Hallucinated stories ("integrate Stripe" → invents files) | Grounding rule: every story must cite codebase evidence or a research source, enforced in the prompt contract and surfaced in review UI. |
| One user (solo dev) — is PO ceremony worth it? | The review screen is one approve click. Cost of the feature when unused: zero (separate entry point). |

**Decision rule used:** the feature is justified because it increases the success rate of the *existing* agent (smaller, better-specified runs), not merely because it adds a new surface.

---

## 2. Product definition

### Persona & job-to-be-done

Solo Flutter developer (current sole persona). JTBD: *"I know what feature I want; turn it into a backlog the agent can execute one ticket at a time, and check my codebase + ecosystem before deciding how."*

### Input contract

Free-text intent, canonical pattern `"Integrate a <X> feature"` (also accepts any feature request). Optional knobs: research depth (`codebase | packages | web`, default `web`), max stories.

### Output contract

1. **Feature brief (mini-PRD):** problem, proposed approach, package/approach decision with rationale + cited sources, out-of-scope list, open questions.
2. **Epics (1–3):** thematic grouping.
3. **Stories (3–12):** each = `title`, user-story sentence, description, acceptance criteria (testable strings — same shape the Verifier already consumes), effort (S/M/L), priority (MoSCoW), dependency order, grounding citations (file paths and/or URLs).
4. **On approval:** selected stories become rows in the existing `tickets` table (source-tagged), grouped under a `feature`, each individually runnable via the existing `/api/code-agent/run` flow.

### End-to-end user flow

```
[Side panel] "New Feature" → intake sheet: "Integrate a ____ feature" + depth
   → PO run starts (SSE progress in activity log: research queries, files found, sources read)
   → [Center pane] Review screen: brief + editable story cards (toggle include, edit AC, reorder)
   → "Approve N stories" → tickets appear in queue grouped by epic
   → user runs each ticket through the existing planner→dev→verifier→qa pipeline
```

Human-in-the-loop is mandatory (mirrors the existing `interrupt_after=["planner"]` philosophy): **nothing enters the backlog without review.**

---

## 3. UX / UI plan (Flutter desktop, `ui/code_agent_flutter`)

### 3.1 Intake (side panel)

- `+ New Feature` button above the ticket queue in `side_panel.dart`.
- Sheet: multiline text field (hint: *"Integrate a … feature"*), research-depth segmented control (`Codebase / + Packages / + Web`, default `+ Web`), max-stories stepper (default 8), `Research` CTA.
- While running, the feature appears in the queue as a distinct card with state `Researching…` (animated), then `Needs Review` (reuse the indigo "needs you" treatment introduced in Phase-1 B2).

### 3.2 PO progress view

- Reuse `activity_log_section.dart` / `run_activity` stream: entries like `Searching web: "flutter in-app purchase packages 2026"`, `Read pub.dev: in_app_purchase`, `Grepped codebase: 14 files`, `Drafting stories…`.
- Cancellable client-side ("Stop watching" semantics, consistent with B4); server cancel is a later story (E5-S3).

### 3.3 Review screen (center pane — the core new surface)

Layout top-to-bottom:

1. **Brief header:** feature title, 3–5 sentence summary, approach decision chip (e.g. `package: in_app_purchase ^3.x`), out-of-scope list, collapsible **Sources** (URLs + codebase files — every claim clickable).
2. **Story cards (grouped by epic):** checkbox (include/exclude), title, story sentence, AC list (inline-editable), effort + priority chips, dependency badge (`after #2`), grounding footnote. Drag to reorder.
3. **Action bar:** `Approve 6 of 8 stories` (primary) / `Reject` / `Re-research with notes` (sends edit notes back through the draft step once — bounded loop).
4. **Empty/honest states:** no fabricated content anywhere (B1 lesson). If research found nothing for a story, the card says so.

### 3.4 Backlog integration

- Queue groups tickets under a collapsible epic/feature header; generated tickets get a small `PO` source badge.
- Ticket detail (center pane) renders stored acceptance criteria — **fixes B1 properly**: criteria now exist at ticket level instead of only appearing after planning.
- Dependency hint: if ticket #2 depends on #1 (not merged), show a non-blocking warning before `Start agent`.

### New/changed UI files

| File | Change |
|---|---|
| `lib/src/feature/code_agent/model/feature_intake_models.dart` | NEW — FeatureIntake, DraftStory, ResearchSource models |
| `lib/src/feature/code_agent/service/code_agent_api_client.dart` | + intake endpoints, SSE for intake events |
| `lib/src/feature/code_agent/view_model/code_agent_vm.dart` | + intake state machine (researching / needs_review / approved) |
| `lib/src/ui/side_panel.dart` | + New Feature button, feature cards, epic grouping |
| `lib/src/ui/feature_review_pane.dart` | NEW — review screen |
| `lib/src/ui/center_pane.dart` | render persisted AC on tickets; PO badge |

---

## 4. Backend architecture

### 4.1 PO workflow (new, small LangGraph in `app/orchestration/`)

```
intake_parse → research (codebase ∥ packages ∥ web) → synthesize_brief
   → decompose_stories → ground_check → [interrupt: human review]
   → commit_tickets (on approve)
```

- **intake_parse** — extract feature noun, candidate search terms (reuse `normalize_search_terms`), classify domain.
- **research** — fan-out, each branch capped and logged to `run_activity`:
  - *codebase:* existing `discover_context()` + `/api/query` RAG search (project must be ingested; if not, degrade gracefully and note it in the brief).
  - *packages:* pub.dev REST API (`pub.dev/api/search`, `/api/packages/{name}`) — no key required; collect score, maintenance, null-safety, platform support.
  - *web:* new `web_research` tool (§4.4) — search + fetch + per-source summary with URL citations.
- **synthesize_brief** — JSON contract: `{summary, approach, package_choice{name, version, rationale, alternatives[]}, out_of_scope[], open_questions[], sources[]}`.
- **decompose_stories** — JSON contract per story: `{epic, title, story, description, acceptance_criteria[], effort, priority, depends_on[], grounding[]}`. Prompt rules: each story ≤ ~5 files of expected change, independently verifiable by `dart analyze` + its AC, ordered so each leaves the app shippable. Follows target project's `AGENTS.md` via `project_guide` exactly like the Planner does.
- **ground_check** — deterministic post-pass: drop/flag stories whose `grounding` cites paths absent from the discovery bundle and lacking any research source; validate JSON against Pydantic schemas (same "keep prompt contract aligned with state keys" convention as AGENTS.md mandates).
- **commit_tickets** — transactional bulk insert on approval.

Runs as an async task with an `intake_id`, reusing `run_activity.append_activity` and the existing SSE event pattern (`runner.py` is the template). It does **not** enter the dev/verifier graph.

### 4.2 Data model (SQLite, `project_ticket_store.py` — additive migration via the existing `_ensure_*_columns` pattern)

New table `features`:

```sql
CREATE TABLE features (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  request_text TEXT NOT NULL,
  status TEXT NOT NULL,            -- researching | awaiting_review | approved | rejected | failed
  brief_json TEXT,                 -- PRD brief incl. sources
  draft_stories_json TEXT,         -- pre-approval drafts (editable)
  research_depth TEXT NOT NULL,    -- codebase | packages | web
  error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
```

New columns on `tickets` (all nullable → backward compatible):

```
feature_id INTEGER, acceptance_criteria TEXT (JSON array), priority TEXT,
effort TEXT, order_index INTEGER, depends_on TEXT (JSON ids), source TEXT  -- manual | po_agent
```

`TICKET_TYPES` gains `"story"`. Existing unique `run_id` index unchanged (story ↔ run stays 1:1).

### 4.3 API (extend `app/api/code_agent/projects_routes.py` + `schemas.py`)

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/code-agent/projects/{id}/features` | POST | Start intake `{request_text, research_depth?, max_stories?}` → `{feature_id}` |
| `/api/code-agent/projects/{id}/features` | GET | List intakes for project |
| `/api/code-agent/features/{id}` | GET | Status + brief + draft stories |
| `/api/code-agent/features/{id}/events` | GET | SSE progress (same envelope as run events) |
| `/api/code-agent/features/{id}/stories` | PUT | Save edited drafts (pre-approval) |
| `/api/code-agent/features/{id}/approve` | POST | `{story_ids[]}` → bulk-create tickets, status=approved |
| `/api/code-agent/features/{id}/reject` | POST | Discard drafts |
| `/api/code-agent/features/{id}/re-research` | POST | `{notes}` → one bounded redraft pass |

When a story-ticket's run starts, the run request enriches `user_request` with the stored description + AC + brief excerpt, so the existing Planner receives PO context without any graph change.

### 4.4 New tool: `app/tools/web_research.py`

- Provider-pluggable search (config `SEARCH_PROVIDER`, `SEARCH_API_KEY` — e.g. Tavily/Bing/Brave) + bounded fetch (allowlist scheme/https, size cap, timeout, strip to text) + LLM per-source digest with mandatory URL attribution.
- Registered in `app/tools/registry.py`; structured dict outputs (repo convention).
- pub.dev client lives beside it (`package_research.py`) — keyless, cached.

### 4.5 Config additions (`app/core/config.py`, `.env.example`)

```
PO_INTAKE_ENABLED=true            PO_RESEARCH_DEPTH_DEFAULT=web
SEARCH_PROVIDER= / SEARCH_API_KEY=
PO_MAX_WEB_QUERIES=6              PO_MAX_PAGE_FETCHES=8
PO_MAX_STORIES=12                 PO_RESEARCH_CACHE_TTL_H=24
PO_CHAT_MODEL=                    # defaults to OPENAI_CHAT_MODEL
```

---

## 5. What is explicitly OUT of scope (v1)

- Jira/Linear/GitHub export (deliberately deferred; internal store chosen as sink).
- Auto-starting agent runs after approval (human starts each run — keeps the existing trust model).
- Multi-user roles, comments, sprint planning, estimation poker, roadmaps.
- Cross-feature dependency management; server-side cancellation of intake (joins the existing cancellation tier).
- Re-research loops beyond one bounded pass.

---

## 6. Cost, latency, failure containment (web depth chosen as default — this section is the price tag)

- Budget per intake at defaults: ≤ 6 search calls, ≤ 8 page fetches, ~5–8 LLM calls (parse, per-branch digests, brief, stories). Estimated wall time 60–120 s — acceptable because it replaces minutes of hand-writing tickets and is fully observable via SSE.
- Research cache keyed `(query, day)` under `data/`; pub.dev responses cached 24 h.
- Any research branch failing (no API key, network down) degrades that branch with an explicit note in the brief — never blocks intake. With no `SEARCH_API_KEY`, depth silently degrades to `packages`.
- Token usage logged per phase via the existing usage hooks in `chat_completion_json`.

---

## 7. Backlog — epics, stories, tickets

Estimates: S ≤ ½ day, M ≈ 1 day, L ≈ 2–3 days. Priority: MoSCoW. **MVP cut line marked.**

### EPIC E1 — Persistence & API foundation

| ID | Story | AC (abridged) | Effort | Pri |
|---|---|---|---|---|
| E1-S1 | As a dev, I want `features` table + ticket columns migrated additively so existing data keeps working. | Migration idempotent (`ensure_schema` pattern); old tickets unaffected; `pytest tests/test_ticket_store*` green incl. new migration tests. | M | Must |
| E1-S2 | As a client, I want intake CRUD endpoints (`POST/GET features`, `GET feature`, `reject`) with Pydantic schemas. | OpenAPI shows routes; 404/409 paths tested; schemas in `schemas.py`. | M | Must |
| E1-S3 | As a client, I want `PUT /stories` + `POST /approve` to transactionally create tickets. | Approve inserts N tickets with `source=po_agent`, `feature_id`, AC JSON, `order_index`; partial-selection works; double-approve returns 409. | M | Must |
| E1-S4 | As a user, I want ticket AC/priority/effort returned in ticket APIs so the UI can render real data. | `TicketResponse` extended (nullable fields); existing UI unaffected. | S | Must |

### EPIC E2 — Research tools

| ID | Story | AC | Effort | Pri |
|---|---|---|---|---|
| E2-S1 | Codebase research branch: reuse `discover_context` + RAG query behind one `research_codebase()` service returning a bounded bundle. | Returns files/symbols/summaries ≤ caps; activity logged; works when project not ingested (degraded note). | M | Must |
| E2-S2 | pub.dev package research (`package_research.py`): search + metadata + scoring, cached. | Given "payments", returns ≥3 candidates w/ scores & platforms; cache hit on 2nd call; offline → graceful skip. | M | Must |
| E2-S3 | `web_research.py` tool: provider-pluggable search + bounded fetch + cited digests. | Caps enforced (`PO_MAX_*`); every digest carries source URL; no key → branch skipped with note; tests mock HTTP. | L | Should |
| E2-S4 | Research cache + budget meter persisted per intake. | Re-running same intake within TTL hits cache; token/call counts visible in activity log. | S | Should |

### EPIC E3 — PO orchestration

| ID | Story | AC | Effort | Pri |
|---|---|---|---|---|
| E3-S1 | Intake graph skeleton (`po_graph.py` + runner task): parse → research → brief → stories → pause at review. | State transitions persisted on `features` row; SSE events stream; crash → `failed` + error surfaced. | L | Must |
| E3-S2 | Brief + story JSON prompt contracts with Pydantic validation and retry-on-invalid (1 retry). | Invalid LLM JSON never reaches DB; contracts unit-tested with golden fixtures. | M | Must |
| E3-S3 | `ground_check` pass: flag/drop ungrounded stories. | Story citing nonexistent path w/o web source → flagged in draft JSON; test fixture proves it. | M | Must |
| E3-S4 | Run-enrichment: starting a story-ticket's run injects description+AC+brief into `user_request`. | Planner receives enriched request; no change to `graph.py`; e2e test with stubbed LLM. | S | Must |
| E3-S5 | Bounded `re-research` pass with user notes. | Exactly one redraft per request; notes appear in prompt; new draft replaces old. | M | Could |

### EPIC E4 — Flutter UI

| ID | Story | AC | Effort | Pri |
|---|---|---|---|---|
| E4-S1 | Intake entry: New Feature button + sheet (text, depth, max stories) + API wiring. | `flutter analyze` clean; intake appears in queue as `Researching…` with live activity. | M | Must |
| E4-S2 | Review pane: brief, sources, story cards w/ include-toggles and inline AC editing; Approve/Reject. | Approve creates tickets visible in queue; excluded stories not created; honest empty states (B1 rule). | L | Must |
| E4-S3 | Backlog grouping by epic/feature + `PO` badge + persisted-AC rendering in ticket detail. | Grouped queue; ticket detail shows stored AC (not planner-derived); manual tickets unchanged. | M | Must |
| E4-S4 | Reorder + dependency badges + pre-run dependency warning. | Drag reorder persists `order_index`; dependent ticket shows non-blocking warning on Start. | M | Should |

### EPIC E5 — Hardening & observability

| ID | Story | AC | Effort | Pri |
|---|---|---|---|---|
| E5-S1 | E2E test: scripted intake (stubbed search/LLM) → approve → tickets → run start. | Single pytest exercising the full path; green in CI without network. | M | Must |
| E5-S2 | Telemetry: per-intake token/cost summary in brief footer + logs. | Brief shows "research cost: N calls / ~M tokens"; logged via `get_logger`. | S | Should |
| E5-S3 | Server-side intake cancellation. | `POST /features/{id}/cancel` stops the task, status=`rejected`; UI button truthful (B4 rule). | M | Could |

**MVP cut (Phase A):** E1-S1…S4, E2-S1, E2-S2, E3-S1…S4, E4-S1…S3, E5-S1 — ships codebase+package-grounded intake end-to-end (~10–12 dev-days).
**Phase B:** E2-S3/S4 (full web research), E4-S4, E5-S2.
**Phase C:** E3-S5, E5-S3.

Dependency spine: E1-S1 → E1-S2/S3 → E3-S1 → (E2-* feed E3-S1) → E4-S2 → E5-S1.

---

## 8. Success metrics

- ≥ 70% of generated stories approved without edits (edit-rate proxy for story quality).
- Agent run pass rate (QA-approved) on PO-generated tickets ≥ manual-ticket baseline.
- Intake wall time p50 ≤ 90 s at `web` depth; $ per intake ≤ ~2× a normal run's planning cost.
- 0 fabricated grounding citations escaping `ground_check` in tests.

## 9. Open questions (for next session)

1. Search provider preference (Tavily vs Brave vs Bing) — affects E2-S3 only.
2. Should approval optionally auto-queue the first story's run? (Deferred; trust model says no for v1.)
3. Epic representation: derived label on tickets (chosen, simpler) vs first-class `epics` table — revisit if epics ever need status.
