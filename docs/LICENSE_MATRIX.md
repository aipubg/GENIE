# GENIE — LICENSE & PROVENANCE MATRIX

**Status:** v1 (verified by direct inspection of each zip's LICENSE file, 2026-09-16)
**Rule:** "Open source" ka matlab "jo chaho copy kar lo" **nahi** hai. Is matrix ke bina koi bhi repo
`third_party/` ya `integrations/` mein integrate nahi ki jayegi.

> ⚠️ Ye legal advice nahi hai. Ye engineering risk-triage hai. Distribution/commercial decision se pehle
> qualified legal review zaroori hai.

---

## 1. Risk classes

| Class | Licenses | GENIE rule |
|---|---|---|
| 🟢 **GREEN** | MIT, Apache-2.0, BSD | Dependency / adapter / extraction allowed. Attribution + LICENSE carry karo. |
| 🟡 **YELLOW** | Custom community license, Sustainable Use | **External service only.** Code copy nahi. Terms padho, commercial threshold note karo. |
| 🟠 **ORANGE** | GPL-3.0 | **Separate process / standalone service.** GENIE ke saath link nahi, derivative bundle nahi. Agar distribute karna ho to source-offer obligation aa jaati hai. |
| 🔴 **RED** | AGPL-3.0 | **Hard isolation.** Network-use bhi copyleft trigger karta hai. Preferred action = **replace**, fallback = alag service jo GENIE se HTTP/CLI par baat kare, kabhi import nahi. |
| ⛔ **BLACK** | No license file | **Code use mana.** Default = all rights reserved. Sirf public docs/API se reference. |

---

## 2. Verified matrix (23 unique repos)

| # | Repo | License (verified) | Class | Role in GENIE | Integration type | Obligation / decision |
|---|---|---|---|---|---|---|
| 1 | `agency-agents` | MIT (AgentLand Contributors) | 🟢 | Agent persona templates | **Reference/extraction** — agent definition library | Attribution. Copy templates into `agents/library`, not runtime code |
| 2 | `ComfyUI` | **GPL-3.0** | 🟠 | Image/video generation | **Isolated service** (remote GPU worker) | No linking, no bundling. Separate process + separate repo. Distributed build → source offer |
| 3 | `Decepticon` | Apache-2.0 | 🟢 | Security agent swarm | **Isolated security env only** (§ spec 53) | Attribution + NOTICE. Kabhi normal assistant scope mein nahi |
| 4 | `deepseek-harness` | MIT (DeepSeek) | 🟢 | Coding harness / CLI agent loop | **Reference** for agent runtime + tool loop | Attribution |
| 5 | `enoch` | Apache-2.0 | 🟢 | Self-improving agent | **Reference** for `evolution/` | Attribution + NOTICE |
| 6 | `hack-skills` | MIT (VillanCh) | 🟢 | Offensive security skills | **Isolated authorized env only** | Attribution. Strict allowlist, audit mandatory |
| 7 | `hermes-agent` | MIT (Nous Research) | 🟢 | Desktop agent (Electron) | **Reference** for computer/UI patterns | Attribution |
| 8 | `HeyGem.ai` | **Silicon Intelligence Community License** | 🟡 | Digital-human / video | **External service only — do NOT bundle** | ⚠️ Commercial trigger: **>1,000 MAU** → license from Silicon Intelligence required. Personal use ok, product use risky → **prefer replacement** |
| 9 | `Kronos` (x2, duplicate) | MIT (ShiYu) | 🟢 | Finance/market foundation model | **External worker** (`integrations/kronos`) | Attribution. Local heavy model forbidden on core → remote worker |
| 10 | `MiroFish` | **AGPL-3.0** | 🔴 | Social simulation | **Hard-isolated service** or replace | ⚠️ Network use = copyleft. Never import. Replace preferred |
| 11 | `n8n` | **Sustainable Use License** + `LICENSE_EE` | 🟡 | Workflow automation | **External service** (`integrations/n8n`) | ⚠️ `.ee` files **unlicensed** — exclude. Non-main branches unlicensed. No reselling as workflow product |
| 12 | `OmniVoice` | Apache-2.0 | 🟢 | Voice/TTS stack | **External worker** (spec §2.3: no local generative model on core) | Attribution + NOTICE |
| 13 | `Open-Higgsfield-AI` | **NO LICENSE FILE** | ⛔ | — | **BLOCK code integration** | Repo itself says source closed. Sirf public API/docs reference, aur wo bhi verify karke |
| 14 | `openclaw` | MIT (OpenClaw Foundation) | 🟢 | Agent platform + **Android app** | **Reference/adapter** — Android node + agent runtime patterns | Attribution. Android bridge ke liye sabse useful reference |
| 15 | `pinchtab` | MIT (Luigi Agosti) | 🟢 | Browser control | **Adapter** → `browser/providers/` (spec §5.5) | Attribution + THIRD_PARTY_LICENSES carry |
| 16 | `public-apis` | MIT | 🟢 | API catalog | **Data/reference** | Attribution |
| 17 | `Qwen-AgentWorld` | Apache-2.0 | 🟢 | Agent evaluation world | **Dev lab only** (`tests/simulation`) — production mein nahi | Attribution + NOTICE |
| 18 | `spec-kit` | MIT (GitHub) | 🟢 | Spec-driven development | **Dev process tooling** (Phase 0 workflow) | Attribution |
| 19 | `VoiceStudio` | **AGPL-3.0** | 🔴 | Voice studio app | **AVOID** — OmniVoice (Apache-2.0) already covers need | ⚠️ AGPL. Drop unless unique capability proven |
| 20 | `youtu-agent` | MIT (Tencent, MIT terms) | 🟢 | Agent framework | **Reference** for agent runtime | Attribution |
| 21 | `youtube-automation-agent` | MIT | 🟢 | YT content pipeline | **Reference** for `media/` specialist (spec §49) | Attribution |
| 22 | `munder-difflin` | MIT (Chaitanya Giri) + `LICENSE-ASSETS` | 🟢 | Illustration/skill pack | **Reference** — note: **assets ka alag license** (`LICENSE-ASSETS`) | Code MIT, **assets alag se check karo** |
| 23 | `deepseek-harness` … | (see #4) | | | | |

**Duplicate:** `Kronos-master (1).zip` = same MIT file as `Kronos-master.zip`. Ek hi entry rakho, doosra delete/ignore karo.

---

## 3. Hard rules

1. 🔴/🟠 repos GENIE process mein **import nahi** honge. Sirf network/CLI boundary par, alag repository, alag license header.
2. ⛔ `Open-Higgsfield-AI` — koi code copy nahi.
3. `n8n` ke `.ee` files aur non-master branch content **exclude** (explicitly unlicensed).
4. `HeyGem.ai` — agar product 1,000 MAU cross kare to license lena hoga → **abhi se replacement plan rakho**.
5. Har integrated component ke saath `SOURCE.md` + `LICENSE` + upstream version + modified-files list (spec §0.2 / §8.15).
6. Personal private use vs distributed/commercial build ke obligations **alag** hain — decision column mein likho.
7. Assets (images/models/voices) ka license code license se alag hota hai (`munder-difflin` case). Dono check karo.

---

## 4. Provenance file template (`third_party/<component>/SOURCE.md`)

```markdown
# <component>

- Upstream: <url>
- Upstream version / commit: <...>
- License: <SPDX id>
- Local modifications: <list of files or "none">
- Integration type: dependency | service | adapter | reference
- GENIE adapter location: <path>
- Distribution impact: none | attribution | source-offer | copyleft | blocked
- Verified by / date: <name, date>
```

---

## 5. Immediate actions

- [ ] `Open-Higgsfield-AI-main.zip` ko "do not use" mark karke archive mein tag karo
- [ ] `Kronos-master (1).zip` duplicate delete/ignore karo
- [ ] `ComfyUI`, `MiroFish` ke liye alag repository + process boundary design karo (Phase 11)
- [ ] `VoiceStudio` (AGPL) drop decision owner se confirm karo — OmniVoice kaafi hai
- [ ] `n8n` extract karte waqt `.ee` exclusion script likho
- [ ] `HeyGem` replacement shortlist banao (Phase 11)
- [ ] Har GREEN repo ka `SOURCE.md` usi waqt banao jab wo pehli baar use ho
