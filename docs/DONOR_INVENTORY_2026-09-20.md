# Donor repository inventory (§15) — 2026-09-20

Surveyed, not assumed. `E:/G3/repos` holds **27 repositories, ~1.87 GB**. All ten
donors named in the roadmap are present. Nothing here has been merged into GENIE
by this survey — this is a map of what exists and what is already wired.

## The ten named in the roadmap

| Roadmap name | Directory | MB | Licence | Stack | Status in GENIE |
|---|---|---|---|---|---|
| Prime | `prime-agent-main` | 23.6 | none found | — | **integrated** — `integrations/prime_rlm.py`, `prime_launcher.py`; also vendored at `vendor/prime_rlm` |
| Scrapling | `Scrapling-main` | 4.6 | none found | — | **integrated** — `integrations/scrapling_{adapter,markdown,sanitizer,spider}.py` |
| Strix | `strix-main` | 9.5 | apache | python | **vendored** — `vendor/strix-audit` + `integrations/strix_{launcher,runtime}.py`; its own tests are excluded from our suite |
| MiroFish | `MiroFish-main` | 9.1 | mit | node | **integrated** — `integrations/mirofish_worker.py`. Reported honestly as *contract tested, upstream NOT LIVE* |
| OpenClaw | `openclaw-main` | 590.8 | mit | node | **NOT REFERENCED** — no integration, no adapter, no mention in source |
| Hermes | `hermes-agent-main` | 175.2 | mit | node+python | **NOT REFERENCED** — the only grep hits are false positives (numpy `hermite_e`) |
| DeepSeek Harness | `deepseek-harness-master` | 92.1 | mit | node | **referenced** — `plugins/sdk.py`. No dedicated adapter in `integrations/` |
| Youtu | `youtu-agent-main` | 8.9 | mit | python | **referenced** — director (`nedle2.py`, `semantic_guard.py`) and media plugin |
| Agency | `agency-agents-main` | 4.7 | mit | — | **integrated** — `agents/factory.py`, `agents/personas.py` |
| Qwen | `Qwen-AgentWorld-main` | 4.5 | apache | — | **referenced** — `core/db.py`, `evaluation/lab.py` |

Two of the ten — **OpenClaw and Hermes** — are present on disk but completely
unused. They are the largest two by a wide margin (766 MB combined).

## The other seventeen

| Directory | MB | Licence | Stack |
|---|---|---|---|
| `HeyGem.ai-main` | 310.2 | other | node |
| `n8n-master` | 207.2 | other | node |
| `munder-difflin-main` | 120.0 | mit | node |
| `VoiceStudio-main` | 69.5 | mit | node+python |
| `ComfyUI-master` | 47.9 | mit | python |
| `Decepticon-main` | 40.1 | apache | node+python |
| `agentscope-main` | 16.1 | apache | python |
| `Kronos-master` | 16.8 | mit | python |
| `pinchtab-main` | 17.7 | mit | go |
| `Open-Higgsfield-AI-main` | 18.6 | none found | node |
| `spec-kit-main` | 12.4 | mit | python |
| `deer-flow-main` | 54.5 | mit | — |
| `enoch-main` | 3.8 | apache | python |
| `hack-skills-main` | 4.0 | mit | — |
| `OmniVoice-master` | 1.3 | apache | python |
| `youtube-automation-agent-master` | 2.0 | mit | node |
| `public-apis-master` | 0.1 | mit | — |

## What this survey does and does not say

**Does say:** what is on disk, what licence it carries, which stack it is, and
whether GENIE's source already references it.

**Does not say:** whether any of it works, whether it is safe to run, or what
GENIE should adopt next. None of these repos has been executed, tested or
security-reviewed here.

**Licence caution:** three carry no licence file at all (`prime-agent-main`,
`Scrapling-main`, `Open-Higgsfield-AI-main`) and two are detected as "other"
(`HeyGem.ai-main`, `n8n-master`). Absence of a licence file is not permission.
`docs/LICENSE_MATRIX.md` is the place these must be resolved before any of that
code reaches the product.

## Still missing

The Agent Maker PDF guides the owner referred to were **not found** anywhere in
the workspace. Nothing has been assumed or fabricated about them — they need to
be supplied before that part of the roadmap can be scoped.
