# External Agent Runtime Images

ResearchForge treats OpenHands and mini-SWE-agent as replaceable executors and
uses them before the compatibility Native Runtime when
`RESEARCHFORGE_AGENT_BACKEND=auto`. The selection order is
`mini-SWE-agent -> OpenHands -> LangGraph`. The platform owns repository checkout,
sandbox launch, model bridge identity, evidence, tests, Critic, budgets, and
release decisions. The external runtime image only needs the selected CLI and
the language/test dependencies required by the target repository.

The repository includes `backend/Dockerfile.agent-runtime` and
`infra/scripts/build-agent-runtime.ps1` for a pinned mini-SWE-agent image. This
keeps the upstream agent out of the API/Worker image and makes its version and
security scan explicit. OpenHands can be supplied through a separately pinned
Agent Server or image using the same adapter contract.

## Image requirements

Build an image from a pinned base and include all of the following:

- The selected external Runtime CLI.
- `git` and the repository language/runtime dependencies.
- A non-root user compatible with `RESEARCHFORGE_SANDBOX_USER` when configured.
- No provider API key baked into the image.

Use `RESEARCHFORGE_SANDBOX_IMAGE` to select that image. The default
`python:3.12-slim` image is intentionally not assumed to contain an external
Runtime CLI.

## Model route

Use `RESEARCHFORGE_AGENT_MODEL_MODE=gateway` in production. The runtime gets a
short-lived bridge credential only as process environment and sends model calls
to `RESEARCHFORGE_AGENT_BRIDGE_URL`. For Docker Desktop, the bridge can be
reachable at `host.docker.internal`; for Compose and Kubernetes, configure an
address reachable from the selected sandbox network or namespace.

`RESEARCHFORGE_SANDBOX_NETWORK_ENABLED=1` is required for a containerized
external runtime using the bridge. Set `RESEARCHFORGE_SANDBOX_NETWORK_NAME` only
when the default Docker `bridge` network cannot reach the bridge service.

## Command templates

Set `RESEARCHFORGE_AGENT_COMMANDS` to a JSON object keyed by runtime name when
the selected image uses a non-default CLI. The
valid keys are `openhands`, `mini_swe_agent`, and `openhands_cli`. Each value is
a JSON string array. Supported placeholders are `{task_prompt}`, `{task_id}`,
`{workspace}`, and `{model}`. Keep commands as argument arrays, never a shell
string.

```text
RESEARCHFORGE_AGENT_COMMANDS={"mini_swe_agent":["mini","--task","{task_prompt}","--yolo","--exit-immediately","--model","{model}","--output","{workspace}/.researchforge/trajectory.json"]}
```

Runtime CLI flags evolve independently. Pin the runtime image and set the exact
command template validated against that image instead of relying on a mutable
global installation.
