-- Relational operational projections. store_records remains the canonical,
-- versioned document store during the staged migration; these tables give
-- reporting and operational queries stable typed columns without dual writes.
create table if not exists operational_training_jobs (
    id text primary key,
    workspace_id text not null,
    status text not null,
    method text not null,
    base_model text not null,
    backend text,
    backend_resource text,
    gpu_count integer not null default 0,
    world_size integer not null default 1,
    node_count integer not null default 1,
    sample_count integer not null default 0,
    created_at timestamptz not null,
    updated_at timestamptz not null default now(),
    body jsonb not null
);

create table if not exists operational_model_usage_ledger (
    id text primary key,
    workspace_id text not null,
    model_id text,
    provider text not null,
    model_name text not null,
    source text not null,
    reference_type text not null,
    reference_id text,
    input_tokens integer not null default 0,
    output_tokens integer not null default 0,
    total_tokens integer not null default 0,
    estimated_cost numeric(18, 6) not null default 0,
    billable_cost numeric(18, 6) not null default 0,
    currency text not null default 'USD',
    fallback_used boolean not null default false,
    actor_id text,
    created_at timestamptz not null,
    body jsonb not null
);

create table if not exists operational_model_billing_reconciliations (
    id text primary key,
    workspace_id text not null,
    provider text not null,
    model_name text,
    period_start timestamptz not null,
    period_end timestamptz not null,
    currency text not null,
    invoice_reference text,
    ledger_entry_count integer not null default 0,
    ledger_total_tokens integer not null default 0,
    provider_total_tokens integer,
    estimated_cost numeric(18, 6) not null default 0,
    actual_cost numeric(18, 6) not null default 0,
    actual_cost_usd numeric(18, 6) not null default 0,
    fx_rate_to_usd numeric(18, 8) not null default 1,
    fx_rate_source text not null default 'usd',
    variance_cost_usd numeric(18, 6) not null default 0,
    tolerance numeric(18, 6) not null default 0.01,
    status text not null,
    submitted_by text,
    created_at timestamptz not null,
    body jsonb not null
);

create table if not exists operational_model_billing_imports (
    id text primary key,
    workspace_id text not null,
    provider text not null,
    format text not null,
    source text not null,
    source_sha256 text not null,
    strict boolean not null default true,
    total_rows integer not null default 0,
    imported_rows integer not null default 0,
    rejected_rows integer not null default 0,
    submitted_by text,
    created_at timestamptz not null,
    body jsonb not null
);

create index if not exists operational_training_jobs_workspace_status_idx
    on operational_training_jobs (workspace_id, status, created_at desc);
create index if not exists operational_model_usage_workspace_time_idx
    on operational_model_usage_ledger (workspace_id, created_at desc);
create index if not exists operational_model_usage_provider_time_idx
    on operational_model_usage_ledger (provider, model_name, created_at desc);
create index if not exists operational_model_billing_workspace_period_idx
    on operational_model_billing_reconciliations (workspace_id, provider, period_start desc);
create unique index if not exists operational_model_billing_invoice_idx
    on operational_model_billing_reconciliations (workspace_id, provider, invoice_reference)
    where invoice_reference is not null;
create index if not exists operational_model_billing_import_workspace_time_idx
    on operational_model_billing_imports (workspace_id, created_at desc);

create or replace function researchforge_refresh_operational_projections() returns trigger
language plpgsql as $$
begin
    if tg_op = 'DELETE' then
        if old.collection = 'training_jobs' then
            delete from operational_training_jobs where id = old.entity_id;
        elsif old.collection = 'model_usage_ledger' then
            delete from operational_model_usage_ledger where id = old.entity_id;
        elsif old.collection = 'model_billing_reconciliations' then
            delete from operational_model_billing_reconciliations where id = old.entity_id;
        elsif old.collection = 'model_billing_imports' then
            delete from operational_model_billing_imports where id = old.entity_id;
        end if;
        return old;
    end if;

    if new.collection = 'training_jobs' then
        insert into operational_training_jobs (
            id, workspace_id, status, method, base_model, backend, backend_resource,
            gpu_count, world_size, node_count, sample_count, created_at, updated_at, body
        ) values (
            new.entity_id, coalesce(new.body->>'workspace_id', 'workspace_default'),
            coalesce(new.body->>'status', 'prepared'), coalesce(new.body->>'method', 'sft'),
            coalesce(new.body->>'base_model', ''), new.body->>'backend', new.body->>'backend_resource',
            coalesce(nullif(new.body->>'gpu_count', '')::integer, 0),
            coalesce(nullif(new.body->>'world_size', '')::integer, 1),
            coalesce(nullif(new.body->>'node_count', '')::integer, 1),
            coalesce(nullif(new.body->>'sample_count', '')::integer, 0),
            coalesce(nullif(new.body->>'created_at', '')::timestamptz, now()), now(), new.body
        ) on conflict (id) do update set
            workspace_id = excluded.workspace_id, status = excluded.status, method = excluded.method,
            base_model = excluded.base_model, backend = excluded.backend, backend_resource = excluded.backend_resource,
            gpu_count = excluded.gpu_count, world_size = excluded.world_size, node_count = excluded.node_count,
            sample_count = excluded.sample_count, updated_at = now(), body = excluded.body;
    elsif new.collection = 'model_usage_ledger' then
        insert into operational_model_usage_ledger (
            id, workspace_id, model_id, provider, model_name, source, reference_type, reference_id,
            input_tokens, output_tokens, total_tokens, estimated_cost, billable_cost, currency,
            fallback_used, actor_id, created_at, body
        ) values (
            new.entity_id, coalesce(new.body->>'workspace_id', 'workspace_default'), new.body->>'model_id',
            coalesce(new.body->>'provider', ''), coalesce(new.body->>'model_name', ''),
            coalesce(new.body->>'source', 'direct_api'), coalesce(new.body->>'reference_type', 'model_invocation'),
            new.body->>'reference_id', coalesce(nullif(new.body->>'input_tokens', '')::integer, 0),
            coalesce(nullif(new.body->>'output_tokens', '')::integer, 0),
            coalesce(nullif(new.body->>'total_tokens', '')::integer, 0),
            coalesce(nullif(new.body->>'estimated_cost', '')::numeric, 0),
            coalesce(nullif(new.body->>'billable_cost', '')::numeric, 0), coalesce(new.body->>'currency', 'USD'),
            coalesce(nullif(new.body->>'fallback_used', '')::boolean, false), new.body->>'actor_id',
            coalesce(nullif(new.body->>'created_at', '')::timestamptz, now()), new.body
        ) on conflict (id) do update set
            workspace_id = excluded.workspace_id, model_id = excluded.model_id, provider = excluded.provider,
            model_name = excluded.model_name, source = excluded.source, reference_type = excluded.reference_type,
            reference_id = excluded.reference_id, input_tokens = excluded.input_tokens,
            output_tokens = excluded.output_tokens, total_tokens = excluded.total_tokens,
            estimated_cost = excluded.estimated_cost, billable_cost = excluded.billable_cost,
            currency = excluded.currency, fallback_used = excluded.fallback_used, actor_id = excluded.actor_id,
            body = excluded.body;
    elsif new.collection = 'model_billing_reconciliations' then
        insert into operational_model_billing_reconciliations (
            id, workspace_id, provider, model_name, period_start, period_end, currency, invoice_reference,
            ledger_entry_count, ledger_total_tokens, provider_total_tokens, estimated_cost, actual_cost,
            actual_cost_usd, fx_rate_to_usd, fx_rate_source, variance_cost_usd, tolerance, status,
            submitted_by, created_at, body
        ) values (
            new.entity_id, coalesce(new.body->>'workspace_id', 'workspace_default'),
            coalesce(new.body->>'provider', ''), new.body->>'model_name',
            (new.body->>'period_start')::timestamptz, (new.body->>'period_end')::timestamptz,
            coalesce(new.body->>'currency', 'USD'), new.body->>'invoice_reference',
            coalesce(nullif(new.body->>'ledger_entry_count', '')::integer, 0),
            coalesce(nullif(new.body->>'ledger_total_tokens', '')::integer, 0),
            nullif(new.body->>'provider_total_tokens', '')::integer,
            coalesce(nullif(new.body->>'estimated_cost', '')::numeric, 0),
            coalesce(nullif(new.body->>'actual_cost', '')::numeric, 0),
            coalesce(nullif(new.body->>'actual_cost_usd', '')::numeric, nullif(new.body->>'actual_cost', '')::numeric, 0),
            coalesce(nullif(new.body->>'fx_rate_to_usd', '')::numeric, 1),
            coalesce(new.body->>'fx_rate_source', 'usd'),
            coalesce(nullif(new.body->>'variance_cost_usd', '')::numeric, nullif(new.body->>'variance_cost', '')::numeric, 0),
            coalesce(nullif(new.body->>'tolerance', '')::numeric, 0.01), coalesce(new.body->>'status', 'matched'),
            new.body->>'submitted_by', coalesce(nullif(new.body->>'created_at', '')::timestamptz, now()), new.body
        ) on conflict (id) do update set
            workspace_id = excluded.workspace_id, provider = excluded.provider, model_name = excluded.model_name,
            period_start = excluded.period_start, period_end = excluded.period_end, currency = excluded.currency,
            invoice_reference = excluded.invoice_reference, ledger_entry_count = excluded.ledger_entry_count,
            ledger_total_tokens = excluded.ledger_total_tokens, provider_total_tokens = excluded.provider_total_tokens,
            estimated_cost = excluded.estimated_cost, actual_cost = excluded.actual_cost,
            actual_cost_usd = excluded.actual_cost_usd, fx_rate_to_usd = excluded.fx_rate_to_usd,
            fx_rate_source = excluded.fx_rate_source, variance_cost_usd = excluded.variance_cost_usd,
            tolerance = excluded.tolerance, status = excluded.status, submitted_by = excluded.submitted_by,
            body = excluded.body;
    elsif new.collection = 'model_billing_imports' then
        insert into operational_model_billing_imports (
            id, workspace_id, provider, format, source, source_sha256, strict, total_rows, imported_rows,
            rejected_rows, submitted_by, created_at, body
        ) values (
            new.entity_id, coalesce(new.body->>'workspace_id', 'workspace_default'),
            coalesce(new.body->>'provider', ''), coalesce(new.body->>'format', 'csv'),
            coalesce(new.body->>'source', 'manual'), coalesce(new.body->>'source_sha256', ''),
            coalesce(nullif(new.body->>'strict', '')::boolean, true),
            coalesce(nullif(new.body->>'total_rows', '')::integer, 0),
            coalesce(nullif(new.body->>'imported_rows', '')::integer, 0),
            coalesce(nullif(new.body->>'rejected_rows', '')::integer, 0), new.body->>'submitted_by',
            coalesce(nullif(new.body->>'created_at', '')::timestamptz, now()), new.body
        ) on conflict (id) do update set
            workspace_id = excluded.workspace_id, provider = excluded.provider, format = excluded.format,
            source = excluded.source, source_sha256 = excluded.source_sha256, strict = excluded.strict,
            total_rows = excluded.total_rows, imported_rows = excluded.imported_rows,
            rejected_rows = excluded.rejected_rows, submitted_by = excluded.submitted_by, body = excluded.body;
    end if;
    return new;
end $$;

drop trigger if exists store_records_operational_projection_trigger on store_records;
create trigger store_records_operational_projection_trigger
after insert or update or delete on store_records
for each row execute function researchforge_refresh_operational_projections();

-- Backfill without invoking application code. The trigger keeps these current.
insert into operational_training_jobs (id, workspace_id, status, method, base_model, backend, backend_resource, gpu_count, world_size, node_count, sample_count, created_at, body)
select entity_id, coalesce(body->>'workspace_id', 'workspace_default'), coalesce(body->>'status', 'prepared'), coalesce(body->>'method', 'sft'), coalesce(body->>'base_model', ''), body->>'backend', body->>'backend_resource', coalesce(nullif(body->>'gpu_count', '')::integer, 0), coalesce(nullif(body->>'world_size', '')::integer, 1), coalesce(nullif(body->>'node_count', '')::integer, 1), coalesce(nullif(body->>'sample_count', '')::integer, 0), coalesce(nullif(body->>'created_at', '')::timestamptz, now()), body from store_records where collection = 'training_jobs'
on conflict (id) do nothing;

insert into operational_model_usage_ledger (id, workspace_id, model_id, provider, model_name, source, reference_type, reference_id, input_tokens, output_tokens, total_tokens, estimated_cost, billable_cost, currency, fallback_used, actor_id, created_at, body)
select entity_id, coalesce(body->>'workspace_id', 'workspace_default'), body->>'model_id', coalesce(body->>'provider', ''), coalesce(body->>'model_name', ''), coalesce(body->>'source', 'direct_api'), coalesce(body->>'reference_type', 'model_invocation'), body->>'reference_id', coalesce(nullif(body->>'input_tokens', '')::integer, 0), coalesce(nullif(body->>'output_tokens', '')::integer, 0), coalesce(nullif(body->>'total_tokens', '')::integer, 0), coalesce(nullif(body->>'estimated_cost', '')::numeric, 0), coalesce(nullif(body->>'billable_cost', '')::numeric, 0), coalesce(body->>'currency', 'USD'), coalesce(nullif(body->>'fallback_used', '')::boolean, false), body->>'actor_id', coalesce(nullif(body->>'created_at', '')::timestamptz, now()), body from store_records where collection = 'model_usage_ledger'
on conflict (id) do nothing;

insert into operational_model_billing_reconciliations (id, workspace_id, provider, model_name, period_start, period_end, currency, invoice_reference, ledger_entry_count, ledger_total_tokens, provider_total_tokens, estimated_cost, actual_cost, actual_cost_usd, fx_rate_to_usd, fx_rate_source, variance_cost_usd, tolerance, status, submitted_by, created_at, body)
select entity_id, coalesce(body->>'workspace_id', 'workspace_default'), coalesce(body->>'provider', ''), body->>'model_name', (body->>'period_start')::timestamptz, (body->>'period_end')::timestamptz, coalesce(body->>'currency', 'USD'), body->>'invoice_reference', coalesce(nullif(body->>'ledger_entry_count', '')::integer, 0), coalesce(nullif(body->>'ledger_total_tokens', '')::integer, 0), nullif(body->>'provider_total_tokens', '')::integer, coalesce(nullif(body->>'estimated_cost', '')::numeric, 0), coalesce(nullif(body->>'actual_cost', '')::numeric, 0), coalesce(nullif(body->>'actual_cost_usd', '')::numeric, nullif(body->>'actual_cost', '')::numeric, 0), coalesce(nullif(body->>'fx_rate_to_usd', '')::numeric, 1), coalesce(body->>'fx_rate_source', 'usd'), coalesce(nullif(body->>'variance_cost_usd', '')::numeric, nullif(body->>'variance_cost', '')::numeric, 0), coalesce(nullif(body->>'tolerance', '')::numeric, 0.01), coalesce(body->>'status', 'matched'), body->>'submitted_by', coalesce(nullif(body->>'created_at', '')::timestamptz, now()), body from store_records where collection = 'model_billing_reconciliations'
on conflict (id) do nothing;

insert into operational_model_billing_imports (id, workspace_id, provider, format, source, source_sha256, strict, total_rows, imported_rows, rejected_rows, submitted_by, created_at, body)
select entity_id, coalesce(body->>'workspace_id', 'workspace_default'), coalesce(body->>'provider', ''), coalesce(body->>'format', 'csv'), coalesce(body->>'source', 'manual'), coalesce(body->>'source_sha256', ''), coalesce(nullif(body->>'strict', '')::boolean, true), coalesce(nullif(body->>'total_rows', '')::integer, 0), coalesce(nullif(body->>'imported_rows', '')::integer, 0), coalesce(nullif(body->>'rejected_rows', '')::integer, 0), body->>'submitted_by', coalesce(nullif(body->>'created_at', '')::timestamptz, now()), body from store_records where collection = 'model_billing_imports'
on conflict (id) do nothing;
