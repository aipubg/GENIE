# GENIE — SECURITY & TRUST

Authoritative: master spec §1 (Layer 1) + §8. Implementation quick-reference.

## Permission & Trust Engine (PTE)

Scope grammar: `<domain>:<resource>:<action>[:qualifier]`
Example: `computer:files:write:GENIE/workspace/**` · `device:phone_main:media.control` · `plugin:spotify:play`

| Grant | Lifetime |
|---|---|
| `one_time` | single action |
| `session` | session end |
| `standing` | revocable, audited |
| `conditional` | rule-based |

**Default = deny.** Har grant: `principal, scope, granted_by, granted_at, expires_at, revoked_at?, reason`.

### Destructive action matrix

| Action | Owner | Member/Guest |
|---|---|---|
| Read/observe | allow | allow (scoped) |
| Reversible write in workspace | allow | allow |
| Write outside workspace | confirm | deny |
| Delete/overwrite/uninstall | **confirm + undo plan** | deny |
| System config/services/registry | confirm | deny |
| Financial/purchase/send-message | confirm + dry summary | deny |
| Credential use/export | confirm, raw kabhi display nahi | deny |
| Physical actuation (relay/lock) | confirm | deny |

## Data classes

`PUBLIC · INTERNAL · SENSITIVE · SECRET · RESTRICTED`
`SECRET` → kabhi provider ko nahi. `RESTRICTED` → local only unless explicit grant.

## Secrets vault

- Modules sirf `secret://provider/openai/key` reference rakhte hain.
- Raw value sirf gateway process mein resolve, phir drop.
- Rotation / refresh (OAuth) / revocation / per-use audit mandatory.
- **Kabhi LLM context mein nahi** — chahe user maang le (refuse + explain).

## Untrusted content boundary

Sources: web, PDF, email, downloaded files, guest chat, OCR, third-party plugin output, unknown app screen text.

Controls: provenance tag (`trust=untrusted`) · no instruction inheritance · capability firewall ·
exfiltration confirm · two-phase actions · taint tracking · render-not-execute.

## Model Policy Registry

Per provider: `allowed_data_classes · vision_allowed · code_execution_allowed · regions · retention ·
fallback_allowed_from · max_cost_per_mission · sensitive_topics`.
Violation → hard block + `POLICY_VIOLATION_BLOCKED`.

## Device trust tiers

`owner_primary > owner_secondary > shared > guest > untrusted`.
Untrusted sirf observe/request — act nahi. Revocation → pairing token revoke + outstanding commands cancel + audit.

## Privacy controls

Always-listening toggle (per room) · hardware mute honoured · mandatory camera indicator · per-room sensing policy
(bedroom default OFF) · retention TTL · local-first · "Forget X" hard delete.

## Safe mode & kill switch

Safe boot: kernel + bus + NEDLE2 + read-only memory — **no plugins/devices/providers**.
Triggers: failed update, runaway agent, repeated plugin crash, manual `--safe`.
Kill switch (UI + voice + hotkey): sab agent/device/computer activity turant rok, missions `PAUSED`.

## Audit

`WHO · WHEN · DEVICE · ACTION · WHY · MISSION · RESULT` — append-only, hash-chained, exportable.
Sirf security nahi — debugging ke liye bhi zaroori.

## Security tooling

`Decepticon` + `hack-skills` → sirf **isolated authorized environment** (D-019). Normal assistant scope mein kabhi nahi.
