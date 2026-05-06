---
profile: HK
profile_status: unapproved
worker_id: portfolio_manager
stage: portfolio_decision
---

HK prompt strategy has not been approved for `portfolio_manager`.

Runtime behavior:

- Fail explicitly.
- Do not fallback to US.
- Do not fallback to CN_A.
- Do not treat crypto as equity or HK as another equity profile without explicit approval.
