# OpenBB Declarative Provider Security Evidence

Status: T0 approved for D0/T1, with user-declarative providers disabled for live reports until admission validation exists

Date: 2026-05-17

## Human Decisions

- Human asked whether full SSRF protection would cause too many testing failures.
- Decision: do not relax SSRF protection for any user-declarative provider that can enter a live `/report`.
- Testing-phase compromise: user-declarative providers are disabled for live reports until admission validation is implemented and tested.
- Early implementation may use approved system providers and fixed OpenBB extension endpoints only.

## Plain-Language Policy

During testing, we reduce failure noise by not letting user-entered URLs participate in live reports yet.

We do not reduce the security checks themselves. When user-declarative providers are enabled later, every configured URL must pass the same checks before it can enter the provider catalog.

## Allowed Network Targets

Default allowed scheme:

- `https`

Conditionally allowed:

- `http` only for an explicit approved public domain and only when documented in the provider manifest approval.

Allowed host rule:

- Host must match the approved declarative provider domain allowlist.
- Subdomain matching must be explicit. Example: approving `example.com` does not automatically approve every unrelated suffix that merely ends with those characters.

## Forbidden Targets

Always reject:

- `localhost`
- `127.0.0.0/8`
- `::1`
- private IPv4 ranges
- unique-local IPv6 ranges
- link-local IPs
- multicast and unspecified addresses
- cloud metadata service IPs, including `169.254.169.254`
- non-HTTP(S) protocols
- redirects to any forbidden target
- redirects to a host outside the allowlist

## Required Validation Points

Admission validation must check:

- request URL before the request
- DNS resolution result before the request
- every redirect target before following it
- final response source URL
- provider source role
- credential handling
- rate-limit policy
- license policy
- raw export policy
- schema sample

If any check fails, status must be `rejected` or `quarantined`; it must not enter `enabled_candidate`.

## Testing Policy

Positive tests:

- Use approved public test domains only.
- Do not use localhost, private IPs, or metadata service exceptions as positive fixtures.

Negative tests must prove rejection of:

- localhost
- loopback IP
- private IP
- link-local IP
- metadata service IP
- unapproved protocol
- unapproved domain
- redirect from approved public URL to private target

## Live Report Policy

Until the admission validator and tests exist:

- user-declarative provider manifests may be saved as `draft`
- they must not enter `enabled_candidate`
- they must not enter `RunProviderPlan`
- they must not be visible to OpenClaw worker tools
- they must not affect live `/report`

## Residual Risk

- System providers still need provider-specific license and raw export policy review as they are implemented.
- This document approves the security boundary, not provider coverage.
- If testing later needs a temporary public test domain, it must be added here with owner, expiry, and exact purpose.
