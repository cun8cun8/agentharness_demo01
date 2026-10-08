create table if not exists store_query_index (
    collection text not null,
    entity_id text not null,
    workspace_id text,
    task_id text,
    status text,
    kind text,
    created_at timestamptz,
    search_text text not null default '',
    body jsonb not null,
    primary key (collection, entity_id)
);

create index if not exists store_query_workspace_page_idx
    on store_query_index (collection, workspace_id, status, created_at desc, entity_id desc);
create index if not exists store_query_task_page_idx
    on store_query_index (collection, task_id, status, created_at desc, entity_id desc);
create index if not exists store_query_search_idx
    on store_query_index using gin (to_tsvector('simple', search_text));

create or replace function researchforge_refresh_query_index() returns trigger
language plpgsql as $$
begin
    if tg_op = 'DELETE' then
        delete from store_query_index where collection = old.collection and entity_id = old.entity_id;
        return old;
    end if;
    insert into store_query_index(collection, entity_id, workspace_id, task_id, status, kind, created_at, search_text, body)
    values (
        new.collection,
        new.entity_id,
        new.body->>'workspace_id',
        new.body->>'task_id',
        new.body->>'status',
        coalesce(new.body->>'type', new.body->>'kind'),
        case when new.body ? 'created_at' then (new.body->>'created_at')::timestamptz else null end,
        lower(coalesce(new.body->>'id','') || ' ' || coalesce(new.body->>'title','') || ' ' ||
              coalesce(new.body->>'goal','') || ' ' || coalesce(new.body->>'repo_path','') || ' ' ||
              coalesce(new.body->>'task_id','') || ' ' || coalesce(new.body->>'model_name','') || ' ' ||
              coalesce(new.body->>'error_summary','')),
        new.body
    )
    on conflict (collection, entity_id) do update set
        workspace_id = excluded.workspace_id, task_id = excluded.task_id, status = excluded.status,
        kind = excluded.kind, created_at = excluded.created_at, search_text = excluded.search_text, body = excluded.body;
    return new;
end $$;

drop trigger if exists store_records_query_index_trigger on store_records;
create trigger store_records_query_index_trigger
after insert or update or delete on store_records
for each row execute function researchforge_refresh_query_index();

insert into store_query_index(collection, entity_id, workspace_id, task_id, status, kind, created_at, search_text, body)
select collection, entity_id, body->>'workspace_id', body->>'task_id', body->>'status',
       coalesce(body->>'type', body->>'kind'),
       case when body ? 'created_at' then (body->>'created_at')::timestamptz else null end,
       lower(coalesce(body->>'id','') || ' ' || coalesce(body->>'title','') || ' ' || coalesce(body->>'goal','') || ' ' ||
             coalesce(body->>'repo_path','') || ' ' || coalesce(body->>'task_id','') || ' ' || coalesce(body->>'model_name','') || ' ' ||
             coalesce(body->>'error_summary','')),
       body
from store_records
on conflict (collection, entity_id) do update set
    workspace_id = excluded.workspace_id, task_id = excluded.task_id, status = excluded.status,
    kind = excluded.kind, created_at = excluded.created_at, search_text = excluded.search_text, body = excluded.body;
