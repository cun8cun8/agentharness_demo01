# Coding V1 implementation and acceptance, 2026-10-09

This record separates implemented changes from acceptance evidence. External
acceptance failures remain open; no new production deployment was performed.

## Real-model Golden Tasks

Evidence: `.run/acceptance/v1-clean-ten-20261009.json`.

| Check | Observed result |
| --- | --- |
| Model | qwen-plus, real provider, no Mock |
| Tasks | 10, of which 8 passed |
| V1 success threshold | 70%; observed 80%, passed |
| Real model evidence | 10/10 runs |
| Model fallback / policy violations | 0 / 0 |
| Average trace completeness | 100% |
| Tests modified | No |
| Tokens / estimated cost | 86,282 / 0.172564 USD |
| Summed run duration | 725,246 ms |

The run used a separate PostgreSQL/Redis acceptance instance at port 18011.
The existing application remains at API port 18001 and frontend port 13010.
This result does not establish acceptable performance with the original store's
historical data. The 2026-10-10 scoped persistence fix was separately exercised
against the original store: 513 runs, 3,264 steps and 11,141 audit records.
Ten run-update/step-add samples had median 364.55 ms and maximum 781.42 ms,
with zero full-snapshot writes. Evidence:
`.run/acceptance/historical-postgres-performance-20261010.json`.
This is hot-path latency evidence, not a complete production capacity benchmark.

Price and pagination repairs failed after repeated patches. Their traces showed
that retries used source context from before the applied patch. The runtime now
reads current source after a failed patch or failed validation. An offline
regression exercises a failing intermediate patch followed by a successful repair.
The 8/10 real-model report predates that final context fix; it does not prove 10/10.

## Implemented acceptance controls

- Golden acceptance verifies each run's task/model identity and model-call
  artifacts, checks tests, trace and policy metrics, and totals only this batch's
  usage. Running and queued jobs resume polling.
- Failure reports replace stale success reports and preserve the current failed
  job identity when available.
- Cancelling a Golden batch cancels its active run and prevents subsequent tasks
  from starting.
- Repository acceptance requires real model-call evidence, completed runs,
  positive passing test counts, unchanged tests, no fallback/policy violations,
  and complete trace evidence. Publication additionally requires a clean baseline,
  matching base revision, successful push and an actual draft PR.
- Complete acceptance accepts a JSON array of at least two distinct repository
  targets. It runs ten real Golden Tasks before processing those targets.

```json
[
  {
    "repository_id": "YOUR_FIRST_CONNECTION_ID",
    "repository_goal": "Fix the reproduced defect without editing tests.",
    "repository_test_command": "python -m pytest backend/tests/test_regression.py -q",
    "repository_branch": "researchforge/v1-first-repair"
  },
  {
    "repository_id": "YOUR_SECOND_CONNECTION_ID",
    "repository_goal": "Fix the reproduced defect without editing tests.",
    "repository_test_command": "python -m pytest tests/test_regression.py -q",
    "repository_branch": "researchforge/v1-second-repair"
  }
]
```

```powershell
python backend/scripts/run_complete_acceptance.py `
  --base-url http://127.0.0.1:18001 --model-name qwen-plus `
  --repository-acceptance-file targets.json --require-isolated-sandbox `
  --publish --push --create-pull-request --output .run/acceptance/complete-real.json
```

The complete runner deliberately requires all ten Golden Tasks to pass; this is
stricter than the standalone V1 70% acceptance threshold. Publication flags
authorize branch pushes and draft PR creation. Review the generated changes before
using them. The runner does not merge PRs or automatically undo a publication.

## Real repository repairs and rollback

Two genuine defects were reproduced on separate test branches:

| Repository | Regression |
| --- | --- |
| cun8cun8/agentharness_demo01 | Acceptance CLI failed to resume polling a running job |
| cun8cun8/mediaforge-aigc-production-platform | Nonfinite receipt age limits bypassed freshness checks |

Branch: `codex/v1-acceptance-base-20261009` in each repository. Only regression
tests were uploaded to these branches. GitHub API baselines were checked against
clean local HEAD revisions, enabling explicit API transport despite Git HTTPS failures.

Both repairs passed the strict real-model, tests, policy and trace gates:
Harness used qwen-plus and passed 1/1 tests; MediaForge used qwen3-coder-plus and
passed 5/5. Both had no test edits, no fallback and complete traces. Diffs were
reviewed before publication. Actual draft PRs:
https://github.com/cun8cun8/agentharness_demo01/pull/18
https://github.com/cun8cun8/mediaforge-aigc-production-platform/pull/9
Both were subsequently closed without merging. Independent GitHub API checks
confirmed closed/unmerged state and unchanged default-branch revisions during rollback.
Evidence: `.run/acceptance/real-repair-final-batch.json` and
`.run/acceptance/real-repair-publication-rollback.json`.

MediaForge autonomous attempts failed and remain recorded. Its successful repair
used an operator-supplied minimal candidate, reviewed and applied by the real
coding model and independently validated. This is assisted repair evidence,
not an unassisted benchmark improvement. Its App installation lacked contents
write access, so publication explicitly used the existing Git credential through
the supported credential_ref path; Harness publication used the App installation.
These two repairs ran in the loopback development acceptance service at port
18021, using isolated workspaces. They do not establish production Docker sandbox
isolation; the separate Golden Task run used the Docker acceptance stack.

## Security and release

The v0.2.7 release workflow failed its vulnerability gate. Original reports remain
under `.run/release-v0.2.7/reports/`: API 57, frontend 10, sandbox 0 and trainer 4
fixable HIGH/CRITICAL findings. Those counts describe the old images.

Implemented remediation removes runtime frontend npm/Yarn, upgrades Docker CLI
to 29.9.0 and kubectl to v1.37.1, and updates the trainer dependency set. Actual
offline CPU one-step SFT, DPO and GRPO smoke tests passed; see
`.run/release-v0.2.7/trainer-smoke.log`.

The release workflow scans all four images with Trivy 0.75.0, preserves reports
on failure, and blocks release unless the scanner succeeds and explicitly reports
zero findings. kubectl can be configured through the KUBECTL_VERSION repository
variable; cluster preflight requires same major version and at most one minor
version difference. Terraform requires an explicit supported EKS version.

The v0.2.8 cloud scan reduced findings to API 4, frontend 0, sandbox 0 and trainer 0.
All four API findings were in kubectl's embedded Go libraries. kubectl v1.37.1
was rebuilt from checksum-verified official source with Go 1.27.2 and x/net 0.60.0.
The v0.2.9 candidate failed regression tests and was not published.

The v0.2.10 release passed all gates and was published on 2026-10-10.
Actual downloaded cloud reports under `.run/release-v0.2.10/cloud-reports/`
contain zero fixable HIGH/CRITICAL findings in all four images. Remote one-step
SFT, DPO and GRPO smoke tests also passed. Release workflow:
https://github.com/cun8cun8/agentharness_demo01/actions/runs/38015885502
Release: https://github.com/cun8cun8/agentharness_demo01/releases/tag/v0.2.10
No production cluster was deployed or available for live preflight validation.

## Regression evidence

The final full backend suite passed 341 tests with 11 environment-dependent skips
and no failures in 480.57 seconds. Output is stored in
`.run/acceptance/final-backend-parser-fixed.log`. The v0.2.10 cloud release suite
also passed before image builds and scanning. The real PostgreSQL hot-path and
multiwriter suite passed 10 tests separately, including fresh workspace insertion.
