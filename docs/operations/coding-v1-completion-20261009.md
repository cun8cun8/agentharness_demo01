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
historical data. Full-snapshot writes caused excessive latency there; that
performance issue remains open.

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

## Real repositories: external acceptance still open

Two genuine defects were reproduced on separate test branches:

| Repository | Regression |
| --- | --- |
| cun8cun8/agentharness_demo01 | Acceptance CLI failed to resume polling a running job |
| cun8cun8/mediaforge-aigc-production-platform | Nonfinite receipt age limits bypassed freshness checks |

Branch: `codex/v1-acceptance-base-20261009` in each repository. Only regression
tests were uploaded to these branches. The original default branches were not
changed on GitHub. Repair attempts failed the remote reachability preflight;
no new model repair run, draft PR or PR rollback proof was produced by these attempts.
See `.run/acceptance/v1-harness-repair-20261009.log` and
`.run/acceptance/v1-mediaforge-repair-20261009.log`.

GitHub REST was reachable while Git HTTPS connections failed. Existing older PRs
are not counted as evidence for this acceptance. Required remaining work is to
restore Git transport, run both model repairs, review their diffs, publish draft
PRs, and record rollback of the unmerged drafts without changing default branches.

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

Final four-image scans and a successful new release remain outstanding. Local
scanner acquisition failed due to interrupted downloads. Upgraded dependencies
alone are not evidence of zero remaining vulnerabilities. No new release tag is
being presented as successful, and no production cluster was available for live
preflight validation.

## Regression evidence

The final full backend suite passed 324 tests with 11 environment-dependent skips
and no failures in 160.60 seconds. Output is stored in
`.run/acceptance/backend-final-suite-20261009.log`. The API image was rebuilt with
the final runtime and acceptance scripts. This full-suite result supersedes the
earlier run's outdated cluster-preflight fixture failure.
