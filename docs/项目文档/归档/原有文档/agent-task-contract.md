# Agent Task Contract Gate

This repository requires a written task contract before non-trivial agent work
that can change architecture, storage, data sources, workflow behavior,
runtime dependencies, or performance-critical code.

The contract is a hard gate. If the implementation cannot follow the approved
contract, the agent must stop and ask the human. The agent must not silently
switch to a smaller, safer, faster, or temporary alternative.

## Required Contract Fields

- Human-approved objective
- Approved architecture direction
- Allowed changes
- Forbidden changes
- Stop conditions
- Acceptance evidence
- Final reconciliation checklist

## Required Behavior

Before implementation:

1. Write or update `memory/active-task-contract.md`.
2. State material assumptions.
3. Get explicit human approval for the contract.

During implementation:

1. Check changes against the contract before editing each subsystem.
2. Stop immediately if a stop condition is hit.
3. Do not replace the approved direction with a workaround.

Before final response:

1. Report approved item -> code evidence -> test evidence -> status.
2. Report every deviation plainly.
3. Do not claim completion if acceptance evidence is missing.

