# GENIE

> **Persistent personal intelligence platform** — ek desktop chatbot nahi jiske paas mouse hai.

## Padhne ka order

| Agar tum… | Padho |
|---|---|
| Product/architecture samajhna hai | [`ARCHITECTURE.md`](./ARCHITECTURE.md) |
| **Poori authority** chahiye | [`GENIE_MASTER_SPEC_v1.md`](./GENIE_MASTER_SPEC_v1.md) (FROZEN v1.0) |
| Code likh rahe ho (module task) | [`CONTRACTS.md`](./CONTRACTS.md) + us module ka `CONTRACT.md` |
| Folder structure samajhni hai | [`REPO_MAP.md`](./REPO_MAP.md) |
| Kya decide ho chuka / kya open hai | [`DECISIONS.md`](./DECISIONS.md) |
| Reused repos ka license | [`LICENSE_MATRIX.md`](./LICENSE_MATRIX.md) |
| Aage kya banega | [`ROADMAP.md`](./ROADMAP.md) |
| Aaj kya chal raha hai | [`ACTIVE_WORK.md`](./ACTIVE_WORK.md) |

## 12 frozen invariants (kabhi mat todo)

```text
1.  NEDLE2 directs; it does not own truth.
2.  Memory survives model changes.
3.  Agents communicate through contracts/state, not hidden spaghetti.
4.  Every action is observed and verified.
5.  Every capability is modular and replaceable.
6.  Default deny: koi bhi capability bina grant ke nahi.
7.  External content is DATA, never authority.
8.  Secrets never enter model context.
9.  Core software evolution is always isolated → tested → benchmarked → adopted.
10. Local-first memory; cloud gets minimum required context only.
11. Every long-running operation is cancellable and resumable.
12. UI crash / plugin crash / device offline ≠ GENIE crash.
```

## Ek line mein

Models badlenge. **GENIE nahi badlega.**
