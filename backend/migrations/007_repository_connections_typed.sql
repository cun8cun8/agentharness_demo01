-- Repository connections are a security boundary, not a generic document.
-- Move them to the typed table while keeping historical generic records for
-- unrelated collections intact.

alter table repository_connections
    add column if not exists github_installation_id bigint,
    add column if not exists github_owner text,
    add column if not exists github_repository text,
    add column if not exists updated_at timestamptz not null default now();

-- Older installations allowed the compatibility store to use
-- `workspace_default` before the relational workspace table was populated.
-- Materialize every workspace referenced by legacy repository data before the
-- typed table's foreign key is exercised. This is additive and idempotent.
insert into workspaces (id, name, owner_id, status, created_at)
select workspace_id,
       'Migrated workspace ' || workspace_id,
       'system',
       'active',
       now()
from (
    select workspace_id
    from repository_connections
    where workspace_id is not null and workspace_id <> ''
    union
    select coalesce(body->>'workspace_id', 'workspace_default')
    from store_records
    where collection = 'repository_connections'
    union
    select coalesce(item.value->>'workspace_id', 'workspace_default')
    from app_snapshots snapshot
    cross join lateral jsonb_each(coalesce(snapshot.body->'repository_connections', '{}'::jsonb)) item
    where snapshot.id = 'default'
) legacy_workspaces
where workspace_id is not null and workspace_id <> ''
on conflict (id) do nothing;

insert into repository_connections (
    id, workspace_id, name, provider, url, local_path, default_branch,
    credential_ref, github_installation_id, github_owner, github_repository,
    status, created_at, updated_at
)
select
    entity_id,
    coalesce(body->>'workspace_id', 'workspace_default'),
    coalesce(body->>'name', entity_id),
    coalesce(body->>'provider', 'local'),
    body->>'url',
    body->>'local_path',
    coalesce(body->>'default_branch', 'main'),
    body->>'credential_ref',
    nullif(body->>'github_installation_id', '')::bigint,
    lower(nullif(body->>'github_owner', '')),
    lower(nullif(body->>'github_repository', '')),
    coalesce(body->>'status', 'active'),
    coalesce(nullif(body->>'created_at', '')::timestamptz, now()),
    now()
from store_records
where collection = 'repository_connections'
on conflict (id) do update set
    workspace_id = excluded.workspace_id,
    name = excluded.name,
    provider = excluded.provider,
    url = excluded.url,
    local_path = excluded.local_path,
    default_branch = excluded.default_branch,
    credential_ref = excluded.credential_ref,
    github_installation_id = excluded.github_installation_id,
    github_owner = excluded.github_owner,
    github_repository = excluded.github_repository,
    status = excluded.status,
    updated_at = now();

-- URL-derived identity lets old connection records participate in safe webhook
-- matching before operators edit them in the workbench.
update repository_connections
set github_owner = lower(split_part(regexp_replace(regexp_replace(url, '^https://github.com/', ''), '\\.git$', ''), '/', 1)),
    github_repository = lower(split_part(regexp_replace(regexp_replace(url, '^https://github.com/', ''), '\\.git$', ''), '/', 2))
where lower(provider) = 'github'
  and url like 'https://github.com/%/%'
  and (github_owner is null or github_repository is null);

delete from store_records where collection = 'repository_connections';

create index if not exists idx_repository_connections_github_identity
    on repository_connections (lower(github_owner), lower(github_repository));

create unique index if not exists uq_repository_connections_github_installation
    on repository_connections (github_installation_id, lower(github_owner), lower(github_repository))
    where lower(provider) = 'github'
      and github_installation_id is not null
      and github_owner is not null
      and github_repository is not null;
