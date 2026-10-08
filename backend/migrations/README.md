# ResearchForge Migrations

`001_initial_schema.sql` is the PostgreSQL schema target for the current P0 data model:
tasks, runs, steps, tool calls, artifacts, trace events, strategies, policies, evaluations,
release gates, trace datasets, preference pairs, memory items, approval requests, and audit logs.

The running service defaults to JSON persistence through `RESEARCHFORGE_STORE_BACKEND=json`.
Set `RESEARCHFORGE_STORE_BACKEND=postgres` and `RESEARCHFORGE_POSTGRES_DSN` to use the
transactional PostgreSQL record store backed by `store_records`. On startup, the store applies
these SQL files in filename order and records checksums in `schema_migrations`.

`005_store_query_projection.sql` maintains indexed pagination/search fields. `006_operational_projections.sql`
maintains typed relational projections for training jobs, model usage, billing reconciliation and billing imports
from `store_records`; this supports reporting while the compatibility API is progressively migrated from documents.
