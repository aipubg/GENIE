# Provenance — vendored Prime Agent RLM runtime

Upstream project : PrimeIntellect-ai/prime-agent
Archive          : `E:\G3\tool\tool\prime-agent-main.zip`
Extracted to     : `E:\G3\repos\prime-agent-main\prime-agent-main\`
License          : MIT — Copyright (c) 2025 Mario Zechner, Copyright (c) 2026 Prime Intellect
License file     : `LICENSE.prime-agent` (copied verbatim from upstream `LICENSE`)

## What is vendored

| Upstream path | GENIE path | Modified? |
|---|---|---|
| `prime-agent-runtime/src/rlm/` (whole package) | `vendor/prime_rlm/rlm/` | **NO — unmodified** |

Files: `__init__.py`, `repl.py`, `bash.py`, `harness.py`, `mcp.py`, `mcp_base.py`, `skill.py`,
`_winjob.py`, `repl.md`.

Runtime requirements (upstream `pyproject.toml`): Python >= 3.11, `mcp>=2,<3`, `tyro`.

## How GENIE uses it

GENIE does **not** rewrite this runtime. It **hosts** it: the kernel is a subprocess speaking
JSON-lines frames (`{"event":"host_request","id":...,"data":{...}}` in,
`{"event":"host_reply","id":...}` out). GENIE implements the host side in
`integrations/prime_rlm.py` (`PrimeRlmRuntime.handle_frame`), so:

* the preserved kernel runs as-is (started via `python -m rlm.repl` from this vendor dir), and
* **GENIE remains the authority** — every child model is resolved by GENIE's Provider Gateway
  from a *capability* requirement, never chosen by the kernel.

## Source modules inspected (not vendored, for audit only)

* `packages/agent/src/` — `agent.ts`, `agent-loop.ts`, `proxy.ts`, `types.ts`
* `packages/coding-agent/src/` — `core/autonomous.ts`, `core/compaction/*`,
  `core/agent-session*.ts`, `cli/daemon-*.ts`, `core/agent-traces.ts`, `core/bash-executor.ts`
* `packages/ai/`, `packages/tui/`

## Classification

**PRESERVED (unmodified) + WRAPPED (GENIE is host).** Live kernel spawn additionally requires the
vendored runtime's own dependencies (`mcp`, `tyro`), so live execution is classified
`external_optional`; the deterministic proof drives GENIE's real host logic against provider
doubles and the kernel's frame protocol without credentials or network.
