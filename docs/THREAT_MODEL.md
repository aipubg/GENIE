# GENIE Threat Model

**Status:** documented (2026-09-18) · **Author:** engineering · **Scope:** the GENIE agent runtime,
its tool/executor surface, the skill and MCP extension points, proactivity, voice, memory, and the
new observability layer — as they exist after Phase 14 and the 14.4 donor re-audit plus the
subsequent capability additions (skill scanner, Skill Hub, MCP supervision + runtime, OTel tracing).

This is a **living document**, not a sign-off. Its job is to make the trust boundaries and residual
risk explicit so the next control is an informed choice, not a hope.

---

## 1. Posture statement

GENIE is an **autonomous agent with real-world effectors** (shell, filesystem, network, applications,
voice, device control) running on an owner's machine, authorised by a PTE (Policy / Trust /
Executive) layer. The threat model is therefore closer to "an agent that can act on your behalf" than
to "a chatbot". The dominant risks are **unintended action** (the agent does something harmful it was
not supposed to) and **compromise via untrusted input** (web content, email, documents, third-party
skills, and MCP servers), rather than classic network perimeter breaches.

What is solid today: input *at install time* (skills), connection *availability* (MCP), and a set of
guardrails on tool execution (PTE, safe mode, read-before-write, receipts, a no-progress breaker, and a
wrong-action metric). What is **not** yet solved: runtime prompt-injection from content the agent
reads, execution containment (no sandbox), and telemetry/secret hygiene. Those are the prioritised
gaps below.

---

## 2. Assets to protect

| Asset | Why it matters |
|---|---|
| Owner identity & credentials (API keys, tokens, passwords) | direct financial / account-takeover impact |
| The host machine (filesystem, shell, installed apps) | destructive actions are hard to undo |
| External accounts the agent can reach (email, cloud, social) | blast radius beyond the local box |
| Owner PII in Memory & conversation history | privacy; regulatory exposure |
| The agent's own integrity / authorised scope | a subverted agent acts as the owner |

---

## 3. Adversaries / threat actors

| Actor | Means | Motivation |
|---|---|---|
| Malicious external content | web pages, emails, docs, MCP tool results fed to the agent | redirect the agent to exfiltrate / act |
| Malicious skill author | a skill package installed by the owner | persistent code running with agent privileges |
| Malicious / compromised MCP server | lies about tool schemas, returns poisoned results | tool poisoning, data exfiltration |
| Confused / mis-prompted model | the owner's own instruction + noise | unintended high-impact action |
| Supply chain | vendored personas / skills / MCP servers | third-party code in the trust boundary |
| Insider / shared machine | another local user or process | cross-user data access |

---

## 4. Trust boundaries

1. **Model ↔ agent** — the LLM emits intents; the executor must not trust them blindly (every action is
   authorised, not assumed safe).
2. **Agent ↔ tool/executor** — capabilities are PTE-scoped; capability ids (dots) and PTE scopes
   (colons) are distinct namespaces and must never be conflated (a historical bug).
3. **Installer ↔ skill** — a skill is untrusted code until scanned; the scanner + Skill Hub sit here.
4. **Agent ↔ MCP server** — external process; supervision handles *availability*, not *honesty*.
5. **Agent ↔ external network** — egress is possible; content returned is untrusted input.
6. **GENIE ↔ host OS** — shell/files/network run on the host with only PTE + confirmations; there is
   **no sandbox** today.

---

## 5. Capability attack surface (what each dangerous capability exposes)

- **Shell / filesystem / network** — arbitrary command, file deletion, exfiltration.
- **Skills** — arbitrary code execution at install + runtime.
- **MCP tools** — whatever the connected server exposes; schemas/results are attacker-influenced.
- **Memory / Control Center** — reads/writes owner PII; `sees_my_data` computation must be honest.
- **Proactive notifications / quiet hours** — a channel an attacker-controlled event could abuse to
  spam or spoof.
- **Voice** — barge-in / spoofing / consent bypass.
- **Observability (OTel spans)** — span attributes may contain PII/secrets if not scrubbed before export.

---

## 6. Threat catalogue

Likelihood/impact are relative (H/M/L). "Mitigation" = what exists today; "Residual" = what remains.

| ID | Component | Threat | Impact | Lik. | Existing mitigation | Residual risk | Recommended control |
|----|-----------|--------|--------|------|---------------------|---------------|---------------------|
| T1 | Skills (install) | Malicious skill: `rm -rf`, `del /S /Q`, `shutil.rmtree`, fork bomb, `dd→/dev` | H | M | `security/skill_scanner` P0 **BLOCK**; `skills/hub` registers BLOCK disabled | Static regex is evadable (encoded/indirect) | Sandbox; behavioural monitoring |
| T2 | Skills (install) | Hardcoded secrets / private keys committed in a skill | H | M | scanner P1 CONFIRM (private-key/AWS/hardcoded) | Literal-only; needs human | Secret scanning at runtime; vault |
| T3 | Skills (install) | Prompt-injection in skill prose ("ignore previous instructions") | H | M | scanner flags injection markers (P1) | Only *literal* patterns; obfuscated missed | Content-trust layer |
| T4 | **Runtime input** | Prompt-injection from web/email/doc/MCP results *during operation* | H | **H** | **None at the content layer** (scanner is install-time only) | **High** — agent can be redirected mid-task | Provenance/trust tags; input classification; HITL for high-impact |
| T5 | MCP server | Tool poisoning: lies about tool purpose/schema | H | M | `mcp_runtime` keeps MCP `isError` distinct from transport failure | Content trust not enforced | Server allowlist; schema validation; result sanitisation |
| T6 | MCP server | Exfiltration via tool results / malicious tools | H | M | `mcp_supervision` quarantines *dead* servers and refuses calls | Availability only, not honesty; transport auth left to impl | mTLS / auth on transport; egress policy |
| T7 | MCP availability | Server death → retry storm / leaked pending calls | M | L | supervision: quarantine cooldown, pending-call cleanup, semaphore | Low | — (handled) |
| T8 | Tool executor | Shell/file/network abuse (host compromise) | H | M | PTE scopes, safe mode, confirmations, `staleguard` read-before-write, `receipts`, wrong-action metric | **No sandbox**; many small allowed actions chain | Sandbox / proxy worker (deferred) |
| T9 | Secrets / telemetry | Secrets leak into logs / **OTel spans** (raw attributes) | H | M | scanner literals; logging hygiene partial | `observability/tracing` exports attributes as-is — no scrubbing | Secret/PII redaction in logs + traces |
| T10 | Agent loop | Runaway self-loop / resource exhaustion | M | L | continuation no-progress breaker; batch leases | Low | — (handled) |
| T11 | Memory / PII | Unauthorised read of owner PII; cross-user on shared host | H | M | `ControlCenter` computes `sees_my_data`; memory forget | Access-control model for Memory needs review | Scoped memory ACLs |
| T12 | Voice | Barge-in / spoof / consent bypass | M | M | voice tests (latency, consent) | Needs adversarial-audio testing | Liveness / speaker verification |
| T13 | Supply chain | Vendored personas / skills / MCP servers run with agent privileges | H | M | Skill Hub + scanner reduce, not eliminate | Runtime behaviour unaudited | Sandbox; signed skills |
| T14 | Identity / A2A / IM | Impersonation; unauthorised agent comms; ingress spoofing | M | M | `agents/a2a` boundary (AgentCard/Registry), `pending-live-acceptance` | **IM channels not built** (no ingress auth) | IM channels with auth (deferred) |
| T15 | RAG / Knowledge | Hallucination / poisoned-doc ingestion via file/email | M | M | None — Knowledge Service deliberately separate & deferred | Medium | Grounded retrieval w/ provenance (deferred) |

---

## 7. Mitigations in place (mapped to modules)

- **Install-time skill safety:** `security/skill_scanner` (P0 BLOCK / P1 CONFIRM / P2 INFO) and
  `skills/hub` (BLOCK verdict ⇒ disabled, force required to enable; CONFIRM ⇒ flagged).
- **MCP resilience:** `integrations/mcp_supervision` (explicit state machine, quarantine cooldown,
  pending-call cleanup, concurrency semaphore) and `integrations/mcp_runtime` (initialize/list/call
  over the supervised session; transport pluggable; `isError` distinct from transport failure;
  death only after exhausted reconnects).
- **Tool-execution guardrails:** PTE authorisation, `core/hardening` (safe/offline mode, crash
  recovery), `agents/staleguard` (read-before-write), `agents/receipts` (deterministic acceptance),
  `agents/continuation` (no-progress breaker), `core/action_metrics` (wrong-action rate).
- **Observability:** `observability/tracing` (OTel-shaped spans, OTLP/HTTP-JSON export).
- **Transparency:** `core/controlcenter` (memory / agents / provider data-sharing) and
  `security/findings` (canonical findings + SARIF for CI).

---

## 8. Residual risk — explicit gaps

1. **Runtime prompt injection (T4) is the top gap.** The skill scanner only inspects skill files at
   install time. Content the agent reads *while running* — web pages, emails, documents, MCP tool
   results — is untrusted input with no equivalent gate. A content-trust / provenance layer and
   human-in-the-loop for high-impact actions are the missing controls.
2. **No sandbox (T1/T8).** Destructive and network actions execute on the host under PTE + confirmations
   only. A confused or compromised model can still chain many individually-allowed low-risk actions.
3. **Scanner is static regex (T1/T2/T3).** Encoded/obfuscated commands, environment-variable
   indirection, and multi-stage fetches evade it. It is a gate, not a guarantee.
4. **Telemetry hygiene (T9).** `observability/tracing.to_otel_json` emits span attributes verbatim.
   Without a scrubbing policy, PII/secrets can reach the collector. Define a redaction allow/deny list
   before shipping traces to any external endpoint.
5. **MCP server honesty (T5/T6).** Supervision handles *availability*, not *malice*; transport auth
   (mTLS) is delegated to the transport implementation. Add server allowlisting and result sanitisation.
6. **RAG absent (T15).** No grounded retrieval means the agent may act on hallucinated "facts" or
   ingest poisoned documents fed via file/email.
7. **IM ingress unbuilt (T14).** No authenticated channel ingress yet; the A2A boundary exists but the
   inbound surface is not.

---

## 9. Recommended next controls (priority order)

1. **Content-trust layer for runtime inputs (T4)** — provenance tagging of web/email/doc/MCP content;
   explicit instruction-vs-data separation; HITL for high-impact tool calls.
2. **Sandbox / proxy worker (T1/T8/T13)** — contain shell/file/network execution; this is the single
   biggest residual-risk reducer.
3. **Telemetry secret/PII redaction (T9)** — scrub span/log attributes before export.
4. **IM channels with auth (T14)** — authenticated ingress; closes the spoofing surface.
5. **Knowledge Service with provenance (T15)** — grounded retrieval to counter poisoning/hallucination.
6. **Workspace backends (Docker/E2B) (T8)** — isolation alternative to a local sandbox.

---

## 10. Out of scope

- Specific CVE tracking for third-party dependencies (covered by normal dependency hygiene).
- Physical-device security (phone/handset) — owner-hardware acceptance item.
- The original Phase-14 release gate (golden #2 quiet-machine, rollback on real hardware, wrong-action
  real-usage baseline, Windows installer) — a separate, hardware-gated gate.

---

*Honest note: this model reflects the code as written and tested. Several "mitigations" above are
TESTED (scanner, Skill Hub, MCP supervision/runtime, breaker, metrics) but not yet wired into a
production deploy path; deployment hardening (items in §9) is the remaining work.*
