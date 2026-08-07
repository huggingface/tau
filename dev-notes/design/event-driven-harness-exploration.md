# Event-driven harness exploration

**Status:** exploratory; vocabulary and boundaries are not yet accepted design.

**Date:** 2026-08-07

This note records a direction worth specifying: treat the reusable harness as a
typed event runtime around an agent, rather than treating "agent" and "harness"
as interchangeable names for the provider/tool loop. It preserves the ideas and
open questions needed for a later spec. It does not propose an implementation or
authorize a package split.

## Motivation

Tau already has several properties this direction depends on:

- provider-neutral typed messages and events;
- an append-only, inspectable session record;
- ordinary typed tools with structured results;
- a portable core separated from the coding application; and
- extension hooks and event consumers.

The next question is whether these seams can describe more than direct model and
tool execution. A useful harness should also be able to connect context sources,
history providers, persistence, renderers, telemetry, indexes, notifications,
and other modules without teaching the agent about their implementations.

A plain assistant text response may cause many meaningful reactions even when
tool use is disabled: persistence, rendering, counters, indexing, notifications,
or context bookkeeping. Tool calls are therefore only one source of actions,
not the definition of action or agency.

## Working vocabulary

### Model provider

The model provider is the inference boundary. In its simplest form it maps text
context to an autocomplete response. Tau's richer boundary supplies structured
messages, tool declarations, and relevant context, then streams structured
assistant content and requested actions.

### Agent

An agent is about choice, action, and potential change. It observes relevant
state, expresses intent, interprets results, and iterates through actions that
reduce the remaining problem space. It is a participant in the runtime's event
flow, not merely a tool executor.

### Harness

The harness is the runtime around the agent. It connects inputs, the agent, model
providers, capabilities, and observers. It owns the seams needed to:

- accept and route typed events;
- register publishers, subscribers, hooks, and capability providers;
- assemble relevant context from attached modules;
- route intent to implementations and return results;
- preserve causality across cascaded reactions;
- expose persistence and replay boundaries; and
- support instrumentation without coupling it to agent policy.

The harness should define interfaces and lifecycle rules, not implement every
service itself. A history implementation might be old native code, a database,
plain text search, embeddings, or an LLM-backed retriever. The harness needs a
stable semantic contract for requesting history, registering a provider, and
receiving results; it must not depend on how those results are produced.

### Coding application

`tau_coding` remains the application layer. It can compose the harness with
coding-specific tools, project context, Git-derived state, resource loading,
sessions, commands, renderers, and the TUI. The portable agent and harness must
not import `tau_coding`; the application supplies capabilities from outside.

## Candidate layer model

The explored ownership model is:

```text
tau_coding and other applications
    register tools, context sources, frontends, and policies
                         |
                         v
tau_harness (candidate)
    event routing, lifecycle, hooks, capability registry, causality,
    context assembly, execution routing, persistence/instrumentation ports
              |                              |
              v                              v
tau_agent                             tau_ai providers
    choice, intent, action policy         model inference
```

This is not Tau's current package model. Today `tau_agent` deliberately contains
messages, tools, events, provider contracts, the loop, `AgentHarness`, and
session primitives. A future spec must decide whether the diagram warrants a
new package, a rename, an internal boundary, or no packaging change at all.

Extracting only `harness.py` and `loop.py` would not be a mechanical cleanup:
those modules depend on the adjacent message, event, provider, and tool
contracts. Any package proposal must demonstrate a one-way dependency graph and
must not create a new protocol/core/harness cycle.

## Event-driven interaction

The harness listens for external and internal events. The agent receives relevant
input through that event flow, publishes intent, and causes registered modules to
react. Those reactions may publish further events.

```text
input event
    |
    v
harness routes and records causality
    +--> agent observes state and publishes intent
    +--> context/history providers contribute information
    +--> model provider performs inference when requested
    +--> persistence, UI, logs, counters, indexes, and notifications react
              |
              +--> further typed events
```

This suggests three semantic categories even if they share one transport and
serialization envelope:

- **Facts/events:** immutable observations of something that happened.
- **Intent/commands:** requests for an action that may succeed, fail, or be
  rejected.
- **Queries/contributions:** requests for information, plus correlated results
  that may enrich active context.

Conflating these categories would make ordering, failure, replay, and side-effect
semantics difficult to reason about. A later spec should decide whether the
distinction is represented by separate types, envelope metadata, or another
small explicit mechanism.

## Hooks and context contribution

Observation alone is insufficient. Some integrations must influence execution,
especially during context assembly. A lesson matcher is a representative case:

1. a session-start, user-input, or action event reaches a registered hook;
2. the hook searches attached lesson sources using typed event context;
3. it returns structured additional context and provenance; and
4. the harness includes that contribution in the relevant model context.

The hook should not need to rewrite one opaque prompt string. A structured result
can carry content, source, trigger, diagnostics, and display preferences. Tau's
existing `CustomMessage` may already be an adequate durable representation when
precise attribution is unimportant: it is stored as a normal message entry and
converted to provider-compatible user context. A new session-entry type is not
an assumed requirement.

Hooks and subscribers have different roles:

- active hooks may contribute context, transform input, veto an operation, or
  affect control flow at declared boundaries;
- passive subscribers observe facts for rendering, logging, metrics, indexing,
  or notifications; and
- capability providers answer semantic requests such as "history surrounding
  this item" without exposing their implementation to the agent.

## Turns and steps

The working conversational definitions are:

### Turn

A turn is one complete conversational exchange initiated by user input. It
includes all assistant responses, model calls, tool executions, generated system
or context messages, relevant events, and durable state changes until the causal
work settles. A turn may contain multiple steps.

For a coding application, the resulting Git patch is an important projection of
the state change. It cannot be the portable definition: another application may
change a database, hardware, remote services, or only durable memory. The generic
record should represent state deltas or artifacts, with `tau_coding` supplying
the Git-specific view.

### Step

A step is one internal inference/action cycle within a turn. It includes:

1. preprocessing hooks;
2. one model response generation;
3. publication of assistant content and intent;
4. directly triggered commands or tool execution; and
5. reactions caused by subscribers, including any resulting events or state
   changes assigned to that cycle.

This vocabulary collides with Tau's current event names. Today
`TurnStartEvent`/`TurnEndEvent` surround one assistant response and its tool
results, while `AgentStartEvent`/`AgentEndEvent` surround the complete run. A
spec must address compatibility rather than silently changing those meanings.

## Causality and settlement

Event cascades need traceability. A candidate envelope should be evaluated with
at least these identifiers:

- `event_id` for the individual record;
- `turn_id` for the conversational exchange;
- `step_id` for the inference/action cycle;
- `causation_id` for the event that directly produced this event; and
- `correlation_id` for the wider request or workflow.

The identifiers alone are not enough. The runtime also needs explicit answers
for ordering, delivery, cancellation, backpressure, retries, idempotency, and
settlement. In particular, a turn should not be defined as complete merely
because the assistant emitted no tool calls: event subscribers may have created
additional causal work.

## Constraints carried forward

Any specification derived from this exploration should preserve Tau's existing
architectural rules:

- small layers with one-way dependencies;
- typed events as the frontend and extension contract;
- no CLI, Textual, Rich, or Tau file-layout policy in the portable core;
- typed functions and structured results rather than framework magic;
- append-only, inspectable session history; and
- implementation notes and public documentation that follow shipped behavior.

## Open questions for a specification

1. Which operations remain owned by `tau_agent`, and which belong to the harness?
2. Does the boundary require a `tau_harness` package, or only a clearer internal
   interface and vocabulary?
3. Which active reactions must complete before a step or turn settles, and which
   passive observers may drain asynchronously after durable acceptance?
4. How are commands, facts, queries, and context contributions distinguished?
5. How are multiple providers for the same semantic capability selected,
   ordered, combined, and diagnosed?
6. Which event and hook contracts are public compatibility promises?
7. How are recursive event cascades bounded and cancelled?
8. What must be journaled to make replay explainable without repeating external
   side effects?
9. How would the proposal migrate Tau's current `AgentStart`/`TurnStart`
   vocabulary and extension API?

## Non-goals of this note

- deciding that a new package must be created;
- renaming existing public events;
- specifying a complete event-bus implementation;
- treating tool calls as the only actions an agent or runtime can cause; or
- changing Tau's current behavior before compatibility and migration are
  understood.

The next artifact should be a focused specification that resolves the open
questions with concrete typed interfaces, lifecycle traces, failure cases, and
acceptance criteria before implementation begins.
