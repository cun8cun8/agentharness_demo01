create index if not exists store_records_workspace_status_page_idx
    on store_records (collection, (body->>'workspace_id'), (body->>'status'), (body->>'created_at') desc, entity_id desc);
create index if not exists store_records_task_page_idx
    on store_records (collection, (body->>'task_id'), (body->>'created_at') desc, entity_id desc);
create index if not exists store_records_status_page_idx
    on store_records (collection, (body->>'status'), (body->>'created_at') desc, entity_id desc);
