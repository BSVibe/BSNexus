# BSNexus Architecture

Diagrams render natively on GitHub via Mermaid. Click any code block
header that says "Mermaid" to expand. The diagrams reflect the
post-founder-metaphor codebase (no agent/task/phase tables).

## 1. System context

How BSNexus relates to the rest of the BSVibe ecosystem and the
external runtime. All three sibling services are **optional** — when
disabled or unreachable, BSNexus falls back to a Noop provider and
continues working in degraded mode.

```mermaid
flowchart LR
    User[Founder]
    FE[Frontend<br/>React 19 + Vite<br/>:13100]
    BE[Backend<br/>FastAPI async<br/>:18100]
    PG[(PostgreSQL)]
    RD[(Redis Streams)]
    LLM[LiteLLM<br/>Anthropic / OpenAI / Ollama]

    BSAGE[BSage<br/>knowledge graph]
    BSGW[BSGateway<br/>model routing]
    BSUP[BSupervisor<br/>safety audit]

    User -->|HTTPS| FE
    FE -->|REST + SSE| BE
    BE -->|asyncpg| PG
    BE -->|streams| RD
    BE -->|acompletion| LLM

    BE -.optional.-> BSAGE
    BE -.optional.-> BSGW
    BE -.optional.-> BSUP

    classDef optional stroke-dasharray:5 5,fill:#fff;
    class BSAGE,BSGW,BSUP optional;
```

## 2. Backend module map

The backend is a single async monolith. The diagram groups modules
by responsibility; arrows show "calls into".

```mermaid
flowchart TB
    subgraph api[api/  — HTTP routers]
        conv[conversation.py]
        proj[projects.py]
        req[requests_api.py]
        deliv[deliverables.py]
        decs[decisions.py]
        ins[inside.py]
        intg[integrations.py]
        events[project_events.py SSE]
    end

    subgraph core[core/  — orchestration]
        rextr[request_extractor.py]
        plan[planner.py replanner]
        disp[dispatcher.py 3-phase session]
        roo[run_orchestrator.py]
        adapt[orchestrator_adapter.py LiteLLM tool loop]
        sm[state_machine.py]
        tools[tools.py file_read/write, shell_exec]
        ws[project_workspace.py]
        pevs[project_events.py event bus]
    end

    subgraph sub[core/* sub-modules]
        comp[composer/<br/>PromptAssembler<br/>KnowledgeClient]
        audit[audit/<br/>AuditSink]
        integ[integrations/<br/>TenantIntegrationSnapshot]
        prompts[prompts/<br/>YAML registry + variants]
        store[storage/<br/>git / s3 / local]
    end

    subgraph models[models/ + queue/]
        m[SQLAlchemy ORM]
        q[Redis Streams<br/>worker_dispatch]
    end

    conv --> rextr --> plan
    plan --> disp --> adapt
    adapt --> tools --> ws
    disp --> comp
    disp --> audit
    disp --> integ
    plan --> prompts
    comp --> prompts
    rextr --> prompts
    roo --> sm
    roo --> pevs
    events --> pevs
    api --> models
    core --> models
    disp --> q
```

## 3. Class diagram — composition + audit + integration

These three Protocols are the degradable boundary: every external
service has a real client and a Noop fallback that satisfies the
same Protocol. The factory `resolve_*(cfg)` returns the right one
based on per-tenant config.

```mermaid
classDiagram
    class KnowledgeClient {
        <<Protocol>>
        +search(intent, top_k) list~KnowledgeFragment~
        +fetch(path) str | None
        +backlinks(path) list~str~
    }
    class BSageKnowledgeClient {
        +base_url
        +api_key
        +auth_token
    }
    class NoopKnowledgeClient
    KnowledgeClient <|.. BSageKnowledgeClient
    KnowledgeClient <|.. NoopKnowledgeClient

    class AuditSink {
        <<Protocol>>
        +preflight(run, snapshot) AuditResult
        +emit_post(run, result) None
    }
    class BSupervisorAuditSink {
        +base_url
        +api_key
        +auth_token
        +timeout_ms
        +fail_mode
    }
    class NoopAuditSink
    AuditSink <|.. BSupervisorAuditSink
    AuditSink <|.. NoopAuditSink

    class PromptAssembler {
        +templates: PersonaTemplateRegistry
        +compose(run, knowledge, intent_summary, ...) Composition
    }
    class PersonaTemplateRegistry {
        +pick(intent, tools_available) PersonaTemplate
        +best_score(intent, tools) float
    }
    class PersonaTemplate {
        +name
        +system_prompt_template
        +tools
        +keywords
        +default_fit
        +score_for(intent, tools_available) float
    }
    PromptAssembler --> PersonaTemplateRegistry
    PersonaTemplateRegistry --> PersonaTemplate
    PromptAssembler ..> KnowledgeClient : uses

    class TenantIntegrationSnapshot {
        +bsage: ProviderConfig | None
        +bsgateway: ProviderConfig | None
        +bsupervisor: AuditProviderConfig | None
    }

    class ResolveFactories {
        <<module>>
        resolve_knowledge_client(cfg) KnowledgeClient
        resolve_audit_sink(cfg, auth_token) AuditSink
    }
    ResolveFactories ..> KnowledgeClient
    ResolveFactories ..> AuditSink
    ResolveFactories ..> TenantIntegrationSnapshot
```

## 4. Class diagram — orchestrator core

The execution core: replanner picks the next step, dispatcher
manages session lifecycle, adapter runs the LLM tool loop, tool
calls touch the workspace.

```mermaid
classDiagram
    class ReplanResult {
        +decision: next_step | done | ask_founder
        +founder_message
        +phase_name?
        +phase_direction?
        +question?
        +options?
        +blocking
    }
    class Replanner {
        <<module: planner.py>>
        replan_next_step(request, completed_runs, ...) ReplanResult
    }

    class Dispatcher {
        <<module: dispatcher.py>>
        dispatch_run(run_id, ...) Task
        _dispatch_background(...) async
    }

    class RunOrchestrator {
        +on_run_completed(run, result, audit, ...)
        +schedule_next(run)
    }

    class LiteLLMOrchestratorAdapter {
        +model
        +project_id
        +api_key
        +base_url
        +execute(system_prompt, user_prompt, tools_allowed, history) dict
        -_complete(messages, tools) Any
    }

    class ToolRunLog {
        +project_id
        +invocations
        +errors
        +written: list~FileWrite~
        +shell: list~ShellExec~
    }

    class RunStateMachine {
        +transition(run, to_status, actor, reason)
    }

    Replanner ..> ReplanResult : returns
    Dispatcher ..> Replanner : Phase 0
    Dispatcher ..> LiteLLMOrchestratorAdapter : Phase 2
    Dispatcher ..> RunOrchestrator : Phase 3
    RunOrchestrator ..> RunStateMachine
    LiteLLMOrchestratorAdapter ..> ToolRunLog
    LiteLLMOrchestratorAdapter ..> "core.tools" : execute_tool_call
```

## 5. ER diagram — main schema

Founder-metaphor schema. Retired tables (agents, tasks, phases,
goals, plan_proposals) deleted. New tables in **bold** prefix.

```mermaid
erDiagram
    tenants ||--o{ projects : owns
    tenants ||--o{ tenant_members : has
    tenants ||--o{ tenant_integration_configs : has

    projects ||--o{ requests : "has"
    projects ||--o{ conversation_messages : has
    projects ||--o{ deliverables : "ships"
    projects ||--o{ decisions : "raises"
    projects ||--o{ execution_runs : "executes"

    requests ||--o{ execution_runs : "decomposes into"
    requests ||--o| requests : "supersedes"
    requests ||--o{ deliverables : "produces"
    requests ||--o{ decisions : "raises"

    execution_runs ||--o{ composition_snapshots : "uses"
    execution_runs ||--o{ execution_run_history : "records"
    execution_runs ||--o{ execution_run_activities : "logs"
    execution_runs }o--|| execution_runs : "parent_run_id"

    deliverables ||--o{ deliverable_versions : "versions"
    deliverable_versions }|--|| execution_runs : "created_by_run_id"

    conversation_messages }o--o| requests : "request_id"
    conversation_messages }o--o| execution_runs : "execution_run_id"
```

## 6. Run state machine

```mermaid
stateDiagram-v2
    [*] --> pending
    pending --> running : orchestrator dispatches
    running --> done : success
    running --> blocked : audit denial / LLM error / timeout
    blocked --> running : manual retry (rare)
    pending --> blocked : pre-dispatch failure
    done --> [*]
```

`RunStateMachine.transition()` is the single write path — every
status change writes both an `execution_run_history` row and a
milestone `execution_run_activities` row, and publishes a
`run_transition` event to the per-project SSE stream.

## 7. Sequence diagram — Direction message → Deliverable

End-to-end flow when the founder sends a chat message in the
Direction surface. Highlights the 3-phase session model in the
dispatcher (prepare → LLM → finalize) so the DB pool is released
during the long-running LLM call.

```mermaid
sequenceDiagram
    participant U as Founder
    participant FE as Frontend
    participant API as Conversation Router
    participant EX as RequestExtractor
    participant Disp as Dispatcher (background task)
    participant Plan as Replanner
    participant Adapt as Orchestrator Adapter
    participant LLM as LiteLLM/Ollama
    participant Tools as core.tools
    participant SSE as ProjectEventBus
    participant DB as PostgreSQL

    U ->> FE: type & enter
    FE ->> API: POST /messages
    API ->> EX: classify(message)
    EX -->> API: intent=request, summary=...
    API ->> DB: insert message + Request row
    API -->> FE: 201 (optimistic ack)
    API ->> Disp: schedule(run_id) [asyncio.create_task]
    API ->> SSE: publish_message(ack)

    Note over Disp: Phase 0 — replan
    Disp ->> Plan: replan_next_step(request, completed_runs, ...)
    Plan ->> LLM: replanner system prompt + payload
    LLM -->> Plan: next_step JSON
    Plan -->> Disp: ReplanResult(phase_direction, …)

    Note over Disp: Phase 1 — prepare (DB session)
    Disp ->> DB: persist composition snapshot, start run
    Disp ->> SSE: phase_start

    Note over Disp,LLM: Phase 2 — LLM tool loop (no DB session)
    loop until no tool_calls or wall-clock budget
        Disp ->> Adapt: execute(system, user, tools)
        Adapt ->> LLM: acompletion(messages, tools)
        LLM -->> Adapt: tool_calls
        Adapt ->> Tools: file_read / file_write / shell_exec
        Tools ->> Tools: workspace path resolve
        Tools -->> Adapt: result
    end
    Adapt -->> Disp: { files, summary, cost }

    Note over Disp: Phase 3 — finalize (new DB session)
    Disp ->> DB: write deliverable, transition run → done
    Disp ->> SSE: phase_done + deliverable
    SSE -->> FE: SSE event
    FE -->> U: UI updates
```

## 8. Frontend component tree

```mermaid
flowchart TB
    App[App.tsx]
    App --> Router[react-router]
    Router --> LP[LandingPage]
    Router --> Dash[DashboardPage]
    Router --> Proj[ProjectPage]
    Router --> Settings[SettingsPage]

    subgraph layout
        Sidebar
        Header
        GlobalChat
    end

    Proj --> layout
    Proj --> ProgressView[components/progress/ProgressView]
    Proj --> DecisionsView[components/decisions/DecisionsView]
    Proj --> InsideView[components/inside/InsideView]
    Proj --> FilesView[Files tab]

    GlobalChat --> useProjectEvents
    ProgressView --> useProjectEvents
    DecisionsView --> useProjectEvents

    Settings --> IntegrationsTab[settings/IntegrationsTab]
    IntegrationsTab --> IntegrationCard
```

## 9. Per-tenant integration config

```mermaid
flowchart LR
    UI[Settings / IntegrationsTab]
    API[/api/v1/integrations/]
    DB[(settings table<br/>category=integrations)]
    Snap[TenantIntegrationSnapshot<br/>60s cache]
    KC[KnowledgeClient]
    AS[AuditSink]
    GW[BSGateway via LiteLLM hook]

    UI -->|PATCH| API
    API -->|encrypt api_key| DB
    Snap -->|read+decrypt| DB

    Snap -->|resolve| KC
    Snap -->|resolve| AS

    KC -->|disabled| Noop1[NoopKnowledgeClient]
    KC -->|enabled| BS1[BSageKnowledgeClient]
    AS -->|disabled| Noop2[NoopAuditSink]
    AS -->|enabled| BS2[BSupervisorAuditSink]

    Snap -->|model selection| GW
```

## 10. SSE event types

The `GET /api/v1/projects/{id}/events` endpoint emits typed events.
The frontend `useProjectEvents` hook routes each into the right
react-query cache invalidation.

```mermaid
flowchart LR
    Bus[ProjectEventBus<br/>asyncio.Queue per subscriber]
    Bus --> ready[ready - initial handshake]
    Bus --> hb[heartbeat - 15s liveness]
    Bus --> msg[message - new ConversationMessage]
    Bus --> trans[run_transition - status change]
    Bus --> dlv[deliverable - new or updated]
    Bus --> dec[decision - new or resolved]

    msg -.invalidate.-> q1["queryKey: messages, projectId"]
    trans -.invalidate.-> q2["queryKey: runs, projectId"]
    dlv -.invalidate.-> q3["queryKey: deliverables, projectId"]
    dec -.invalidate.-> q4["queryKey: decisions, projectId"]
```

## See also

- [CLAUDE.md](../CLAUDE.md) — assistant rules + must/never list
- [docs/known-issues/](./known-issues/) — open watches
- [docs/product-direction/](./product-direction/) — strategic notes
