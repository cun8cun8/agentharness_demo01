alter table if exists tasks
    add column if not exists execution_config jsonb not null default '{}'::jsonb;
