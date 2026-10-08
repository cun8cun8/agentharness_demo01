-- Keep Trace dataset workspace joins index-backed. Trace items reference their
-- run through a JSON document field while runs reference tasks through the
-- query projection, so the expression index prevents repeated full scans.
create index if not exists store_query_trace_agent_run_idx
    on store_query_index (collection, ((body->>'agent_run_id')), created_at desc, entity_id desc)
    where collection = 'trace_dataset_items';
