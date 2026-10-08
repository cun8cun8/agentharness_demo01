# Open-source release checklist

This project is released under Apache-2.0 as of the 2026 open-source release.
The canonical license text is in `LICENSE`; `NOTICE` carries the project
attribution and third-party redistribution reminder.

## Before publishing

1. Confirm copyright ownership and contributor permissions for all source,
   generated assets and copied snippets.
2. Review `LICENSE`, `NOTICE`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` and
   `SECURITY.md`. Keep third-party notices with the release artifact.
3. Run `python backend/scripts/oss_preflight.py`, the backend test suite,
   `npm run check`, and `npm run build` in `frontend`.
   For a single aggregated gate and JSON evidence, use
   `python backend/scripts/release_preflight.py` (or
   `infra/scripts/run-release-preflight.ps1` on Windows).
4. Run the production preflight against the intended Kubernetes overlay. Do not
   publish an overlay that still contains image, domain, certificate or storage
   placeholders.
5. Run dependency review, CodeQL and the release workflow. Investigate all
   high or critical findings before publishing.
6. Verify that container images contain SBOM and provenance attestations, and
   retain the release manifest and rollback reference. Production manifests
   must reference immutable image tags or digests; mutable tags such as
   `latest`, `stable` and `main` are rejected by the preflight.
7. Confirm that GitHub App private keys, webhook secrets, model keys, database
   credentials and object-storage credentials are injected by the deployment
   secret manager and are absent from the source archive.
8. Generate and retain a deterministic release manifest. It records the
   Apache-2.0 license hash, dependency lock hashes, source inputs and immutable
   image references:

   ```bash
   python backend/scripts/release_manifest.py \
     --output release-manifest.json \
     --version "$RESEARCHFORGE_VERSION" \
     --require-images \
     --image "api=$API_IMAGE" \
     --image "frontend=$FRONTEND_IMAGE" \
     --image "sandbox=$SANDBOX_IMAGE" \
     --image "trainer=$TRAINER_IMAGE"
   ```

   `--version` is optional; when omitted, the script reads the version from
   `frontend/package.json`. Add `--benchmark-report` when a repository benchmark is part of the release
   gate. A failed benchmark gate or mutable image reference makes the command
   fail before publication.

## Apache-2.0 release gate

Keep Apache-2.0 text and attribution notices in every source archive. Add
appropriate SPDX identifiers to new files and review dependency licenses for
redistribution compatibility. Do not imply that third-party dependencies are
Apache-2.0; their original licenses remain authoritative.
