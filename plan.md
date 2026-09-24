Claro. Te lo dejo en Markdown, listo para guardarlo como `personal-agent-architecture-migration-plan.md`.

# Personal Agent Ecosystem – Architecture & Migration Plan

## Status (as of 2026-09-24)

Most of this plan is already executed, with one deliberate deviation:

* `plan-generator` and `ntc-catalog` are independent repos (git submodules) — done, matches plan.
* `personal-agent` was published as its own repo (this repo) — done, matches plan.
* No single `agent-toolkit` monorepo was created. Instead, each shared capability was extracted
  into its own standalone repo, installed as a git dependency in `requirements.txt`:
  * `intervals-icu-client` (Intervals.icu API access) — used by `coach_tools.py`, `plan_tools.py`,
    `ntc_tools.py`, and `plan_generator`.
  * `pgvector-agent-memory` (Postgres+pgvector connection management, embeddings, generic
    cosine-similarity semantic search) — used by `agent_memory.py`'s `SupabaseAgentMemory`, which
    now subclasses it and keeps only the `training_metrics`/`injury_logs`-schema-specific queries.
* `coach_tools.py`, `plan_tools.py`, `ntc_tools.py` were reviewed function-by-function: all of them
  are legitimate Training-Coach-specific adapters (per this doc's own criteria in Section 5) and
  stay in this repo. No further extraction identified there.
* Not yet done: `agent-toolkit`-style capabilities that don't exist in code yet (weather, routes,
  recovery, calendar) — nothing to extract until an agent actually needs them.

---

## 1. Objective

Create a clean, modular GitHub architecture for the personal agent ecosystem while preserving the existing functionality of `training-coach-agent` and the already advanced local `personal-agent` project.

The key principle is to separate:

* **Orchestration**
* **Domain-specific agents**
* **Reusable agent capabilities**
* **Independent domain projects**

The migration should be incremental so that working functionality is not disrupted.

---

## 2. Target Repository Structure

The proposed GitHub organization is:

```text
eloyrgz/
│
├── personal-agent
├── training-coach-agent
├── agent-toolkit
├── plan-generator
└── ntc-catalog
```

### `personal-agent`

Top-level orchestration layer.

Responsible for:

* Routing requests to the appropriate agent
* Calling the Training Coach Agent
* Calling the general OpenAI agent
* Managing the overall agent architecture
* Providing bridges/adapters between agents

---

### `training-coach-agent`

Domain-specific training agent.

Responsible for:

* Training conversations
* Training-specific memory
* Injury-related agent behavior
* Training-specific tools
* Training plan interaction
* NTC interaction
* API
* Telegram
* Home Assistant integration
* Synchronization

---

### `agent-toolkit`

Shared, reusable capabilities that can be consumed by multiple agents.

Potential examples:

* Weather
* Routes
* Intervals.icu utilities
* Recovery
* Calendar
* Generic training calculations
* Other capabilities that are not specific to a single agent

The goal is for this repository to become a reusable toolbox for the entire personal-agent ecosystem.

---

### `plan-generator`

Independent training-plan generation and analysis engine.

It already has enough functionality and internal structure to be considered a standalone project, including:

* Plan engine
* Plan analyzer
* Plan validator
* Workout library
* Intervals.icu integration
* FIT repair
* CLI
* Database schema
* Tests
* Training data

It should therefore remain independent rather than being moved into `agent-toolkit`.

---

### `ntc-catalog`

Independent Nike Training Club catalog/data project.

Responsible for:

* NTC extraction
* NTC workout catalog
* Catalog data
* Extraction-related documentation

The Training Coach Agent can consume this project through an adapter such as `ntc_tools.py`.

---

# 3. Target Architecture

```text
                         personal-agent
                              │
                            Router
                              │
               ┌──────────────┴──────────────┐
               │                             │
        Training Coach Agent           General OpenAI Agent
               │
       ┌───────┼────────┐
       │       │        │
       ▼       ▼        ▼
 agent-toolkit  plan-generator  ntc-catalog
```

As the system grows:

```text
                         personal-agent
                              │
                            Router
                              │
        ┌─────────────┬───────┼────────┬─────────────┐
        │             │       │        │             │
   Training Agent  Strength  Race   Nutrition    Future Agents
        │             │       │        │
        └─────────────┴───────┴────────┘
                              │
                       agent-toolkit
                              │
                 ┌────────────┼────────────┐
                 │            │            │
              Weather       Routes      Intervals
```

Some specialized agents can additionally use:

```text
plan-generator
ntc-catalog
```

---

# 4. Responsibility Boundaries

## `personal-agent`

The personal agent is the **orchestration layer**.

```text
personal-agent
    │
    └── Router
         ├── Training Coach
         └── General OpenAI
```

It should:

* Decide which agent should handle a request
* Pass the request to the appropriate agent
* Keep the overall architecture independent from individual domains
* Avoid containing detailed training logic

The existing router should remain stable during the initial refactoring.

---

## `training-coach-agent`

The Training Coach is the **training-domain intelligence layer**.

It should contain:

```text
chat_agent.py
injury_agent.py
agent_memory.py
coach_tools.py
plan_tools.py
ntc_tools.py
api.py
telegram_bot.py
sync_pipeline.py
custom_components/
```

Its responsibility is to orchestrate training-related capabilities rather than implementing every underlying capability itself.

---

## `agent-toolkit`

The toolkit contains **reusable capabilities**.

For example:

```text
agent-toolkit/
├── weather/
├── routes/
├── intervals/
├── recovery/
├── calendar/
└── ...
```

A capability should be placed here when:

1. Multiple agents could reasonably use it.
2. It does not depend on a specific agent's prompts.
3. It does not depend on a specific agent's internal memory.
4. It has a reasonably clean standalone interface.

---

# 5. Important Design Principle: Tools vs. Adapters

Do **not** automatically move:

```text
coach_tools.py
plan_tools.py
ntc_tools.py
```

into `agent-toolkit`.

The fact that a file is called `*_tools.py` does not necessarily mean that it belongs in the shared toolkit.

These files can be **adapters between the Training Coach Agent and other capabilities**.

For example:

```text
ntc-catalog
     │
     ▼
ntc_tools.py
     │
     ▼
training-coach-agent
```

And:

```text
plan-generator
     │
     ▼
plan_tools.py
     │
     ▼
training-coach-agent
```

This means:

* `ntc-catalog` contains the NTC data/domain logic.
* `plan-generator` contains the plan-generation engine.
* `ntc_tools.py` and `plan_tools.py` expose those capabilities to the Training Coach.

This keeps the boundaries clean.

---

# 6. Current Repository Mapping

| Current component                                | Target repository      | Action                                         |
| ------------------------------------------------ | ---------------------- | ---------------------------------------------- |
| `personal-agent/app.py`                          | `personal-agent`       | Keep                                           |
| `personal-agent/training-coach-bridge/bridge.py` | `personal-agent`       | Keep                                           |
| `chat_agent.py`                                  | `training-coach-agent` | Keep                                           |
| `injury_agent.py`                                | `training-coach-agent` | Keep                                           |
| `agent_memory.py`                                | `training-coach-agent` | Keep                                           |
| `coach_tools.py`                                 | `training-coach-agent` | Keep initially; review individual capabilities |
| `plan_tools.py`                                  | `training-coach-agent` | Keep as adapter                                |
| `ntc_tools.py`                                   | `training-coach-agent` | Keep as adapter                                |
| `api.py`                                         | `training-coach-agent` | Keep                                           |
| `telegram_bot.py`                                | `training-coach-agent` | Keep                                           |
| `sync_pipeline.py`                               | `training-coach-agent` | Keep                                           |
| `custom_components/training_coach`               | `training-coach-agent` | Keep                                           |
| `plan_generator/`                                | `plan-generator`       | Separate repository                            |
| `ntc_catalog/`                                   | `ntc-catalog`          | Separate repository                            |

---

# 7. Migration Plan

## Step 1 — Create `agent-toolkit`

Create the new repository:

```text
eloyrgz/agent-toolkit
```

Do not move existing production code yet.

---

## Step 2 — Inventory Existing Tools

Review:

```text
coach_tools.py
plan_tools.py
ntc_tools.py
```

Function by function.

Classify each capability as:

```text
Shared capability
Training Coach-specific
plan-generator capability
ntc-catalog capability
Infrastructure
```

This classification should happen before moving code.

---

## Step 3 — Extract the First Shared Capability

Choose one capability that is clearly reusable.

For example:

```text
weather
```

or:

```text
Intervals.icu utility
```

Move the underlying implementation to:

```text
agent-toolkit/
└── <capability>/
```

Give it a clean, independent interface.

---

## Step 4 — Update `training-coach-agent`

Modify the Training Coach to consume the new toolkit capability.

The objective is:

```text
training-coach-agent
        │
        ▼
agent-toolkit
```

while keeping the externally visible behavior unchanged.

---

## Step 5 — Repeat Incrementally

Extract additional shared capabilities one at a time.

After every extraction:

1. Run the tests.
2. Start the Training Coach.
3. Verify the affected functionality.
4. Confirm the agent still behaves as expected.
5. Commit the changes.

Avoid a large "big bang" refactor.

---

## Step 6 — Formalize Independent Projects

Keep these as separate repositories:

```text
plan-generator
ntc-catalog
```

Make their interfaces and dependencies explicit.

The Training Coach should consume them rather than duplicating their implementation.

---

## Step 7 — Publish `personal-agent`

Once the repository boundaries are stable, publish the already advanced local project:

```text
eloyrgz/personal-agent
```

The existing router and bridge should remain essentially unchanged.

---

## Step 8 — Add Future Agents

Once the architecture is stable, new agents can be added without redesigning the whole system.

For example:

```text
personal-agent
    │
    └── Router
         ├── training-agent
         ├── strength-agent
         ├── race-agent
         ├── nutrition-agent
         └── general-agent
```

All of them can potentially reuse:

```text
agent-toolkit
```

and specialized projects such as:

```text
plan-generator
ntc-catalog
```

where appropriate.

---

# 8. Migration Principles

### 1. Do not refactor the router and extract tools at the same time

The existing `personal-agent` router is already working. Keep it stable while restructuring the lower layers.

### 2. Preserve working behavior

After every extraction, verify that the Training Coach behaves exactly as before.

### 3. Avoid duplication

There should be one implementation of a shared capability.

Do not copy the same tool into multiple agents.

### 4. Keep shared capabilities independent

`agent-toolkit` should not depend on:

* Training Coach prompts
* Training Coach memory
* Training Coach-specific state
* The Training Coach router

### 5. Use explicit interfaces

Each shared tool should have a clear input/output contract.

### 6. Version dependencies

As repositories become independent, use versioned dependencies where appropriate.

For example:

```text
agent-toolkit==0.1.0
```

This allows the Training Coach to use a stable version while newer versions are developed.

### 7. Prefer small, reversible changes

Each migration should be independently testable and easy to revert.

### 8. Do not classify files by filename alone

A file named `*_tools.py` does not automatically belong in `agent-toolkit`.

Classify components by their **responsibility and coupling**.

---

# 9. End State

The final ecosystem should look approximately like this:

```text
                         personal-agent
                              │
                            Router
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   Training Agent        General Agent          Future Agents
        │
        ├──────────────────────┐
        │                      │
        ▼                      ▼
  agent-toolkit          plan-generator
        │
        ├── weather
        ├── routes
        ├── Intervals
        ├── recovery
        └── other shared tools

Training Agent
        │
        └──────────────► ntc-catalog
```

The important distinction is:

```text
personal-agent
    = orchestration

training-coach-agent
    = training-domain intelligence

agent-toolkit
    = reusable capabilities

plan-generator
    = training-plan engine

ntc-catalog
    = NTC data/domain project
```

---

# 10. Immediate Next Action

Before moving any code, inspect these three files in detail:

```text
coach_tools.py
plan_tools.py
ntc_tools.py
```

Review their functions individually and classify them.

The goal is to identify the **first capability that is clearly reusable outside the Training Coach Agent**.

That first extraction should establish the conventions for `agent-toolkit` and become the template for subsequent migrations.

**Do not modify the `personal-agent` router yet.**

The safest sequence is:

```text
Current local personal-agent
            │
            │ keep stable
            ▼
     Existing router
            │
            ▼
  training-coach-agent
            │
            │ extract incrementally
            ▼
      agent-toolkit
            │
            ├── shared capabilities
            │
            ├── plan-generator
            │
            └── ntc-catalog
```

This gives the project a clean foundation for adding more specialized agents in the future without duplicating tools or coupling all agents to the Training Coach.
