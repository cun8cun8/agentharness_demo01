create table if not exists store_records (
    collection text not null,
    entity_id text not null,
    body jsonb not null,
    version integer not null default 1,
    updated_at timestamptz not null default now(),
    primary key (collection, entity_id)
);
create index if not exists store_records_workspace_idx
    on store_records (collection, (body->>'workspace_id'));
create index if not exists store_records_run_idx
    on store_records (collection, (body->>'run_id'));
