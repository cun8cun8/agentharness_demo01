create table if not exists app_snapshots (
    id text primary key,
    body jsonb not null,
    updated_at timestamptz not null default now()
);
