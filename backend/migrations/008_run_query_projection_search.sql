-- Keep the generic query projection useful for the run workbench. The first
-- projection migration predates project/branch-aware run records, so this is
-- additive and safe for already initialized PostgreSQL databases.
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
        lower(
            coalesce(new.body->>'id','') || ' ' ||
            coalesce(new.body->>'title','') || ' ' ||
            coalesce(new.body->>'goal','') || ' ' ||
            coalesce(new.body->>'repo_path','') || ' ' ||
            coalesce(new.body->>'task_id','') || ' ' ||
            coalesce(new.body->>'repository_id','') || ' ' ||
            coalesce(new.body->>'project_name','') || ' ' ||
            coalesce(new.body->>'repository_url','') || ' ' ||
            coalesce(new.body->>'branch','') || ' ' ||
            coalesce(new.body->>'default_branch','') || ' ' ||
            coalesce(new.body->>'model_name','') || ' ' ||
            coalesce(new.body->>'agent_strategy_id','') || ' ' ||
            coalesce(new.body->>'error_summary','')
        ),
        new.body
    )
    on conflict (collection, entity_id) do update set
        workspace_id = excluded.workspace_id,
        task_id = excluded.task_id,
        status = excluded.status,
        kind = excluded.kind,
        created_at = excluded.created_at,
        search_text = excluded.search_text,
        body = excluded.body;
    return new;
end $$;

update store_query_index indexed
set search_text = lower(
    coalesce(records.body->>'id','') || ' ' ||
    coalesce(records.body->>'title','') || ' ' ||
    coalesce(records.body->>'goal','') || ' ' ||
    coalesce(records.body->>'repo_path','') || ' ' ||
    coalesce(records.body->>'task_id','') || ' ' ||
    coalesce(records.body->>'repository_id','') || ' ' ||
    coalesce(records.body->>'project_name','') || ' ' ||
    coalesce(records.body->>'repository_url','') || ' ' ||
    coalesce(records.body->>'branch','') || ' ' ||
    coalesce(records.body->>'default_branch','') || ' ' ||
    coalesce(records.body->>'model_name','') || ' ' ||
    coalesce(records.body->>'agent_strategy_id','') || ' ' ||
    coalesce(records.body->>'error_summary','')
)
from store_records records
where indexed.collection = records.collection and indexed.entity_id = records.entity_id;
