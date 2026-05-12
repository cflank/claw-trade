---
profile: US
profile_status: unapproved
worker_id: report_polisher
stage: final_report
---

US report polishing strategy has not been approved for `report_polisher`.

Runtime behavior:

- Fail explicitly.
- Do not fallback to US.
- Do not fallback to CN_A.
- Do not treat US, HK, or crypto as another approved profile without explicit approval.
