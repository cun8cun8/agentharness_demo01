create table if not exists tasks (
    id text primary key,
    workspace_id text not null default 'workspace_default',
    type text not null,
    title text not null,
    repo_path text,
    test_command text,
    test_timeout_seconds integer not null default 120,
    goal text not null,
    execution_config jsonb not null default '{}'::jsonb,
    status text not null,
    budget jsonb not null default '{}'::jsonb,
    latest_run_id text,
    created_at timestamptz not null,
    updated_at timestamptz not null
);

create table if not exists workspaces (
    id text primary key,
    name text not null,
    owner_id text not null,
    status text not null default 'active',
    created_at timestamptz not null
);

create table if not exists users (
    id text primary key,
    workspace_id text not null references workspaces(id),
    email text not null,
    name text not null,
    role text not null,
    status text not null default 'active',
    created_at timestamptz not null
);

create table if not exists repository_connections (
    id text primary key,
    workspace_id text not null references workspaces(id),
    name text not null,
    provider text not null,
    url text,
    local_path text,
    default_branch text not null default 'main',
    credential_ref text,
    status text not null default 'active',
    created_at timestamptz not null
);

create table if not exists agent_runs (
    id text primary key,
    task_id text not null references tasks(id),
    policy_version_id text not null,
    agent_strategy_id text not null,
    model_name text not null,
    status text not null,
    phase text not null,
    started_at timestamptz,
    finished_at timestamptz,
    total_tokens integer not null default 0,
    total_cost numeric(12, 6) not null default 0,
    duration_ms integer not null default 0,
    tool_call_count integer not null default 0,
    error_summary text,
    metrics jsonb not null default '{}'::jsonb,
    created_at timestamptz not null,
    updated_at timestamptz not null
);

create table if not exists agent_steps (
    id text primary key,
    run_id text not null references agent_runs(id),
    step_index integer not null,
    phase text not null,
    goal text not null,
    thought_summary text,
    action text,
    observation text,
    status text not null,
    metadata jsonb not null default '{}'::jsonb,
    started_at timestamptz not null,
    finished_at timestamptz
);

create table if not exists tool_calls (
    id text primary key,
    step_id text not null references agent_steps(id),
    run_id text not null references agent_runs(id),
    tool_name text not null,
    input jsonb not null default '{}'::jsonb,
    output jsonb not null default '{}'::jsonb,
    status text not null,
    duration_ms integer,
    error_message text,
    created_at timestamptz not null,
    finished_at timestamptz
);

create table if not exists artifacts (
    id text primary key,
    run_id text not null references agent_runs(id),
    step_id text,
    type text not null,
    name text not null,
    uri text not null,
    content text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null
);

create table if not exists trace_events (
    id text primary key,
    event_type text not null,
    task_id text not null,
    run_id text not null,
    step_id text,
    timestamp timestamptz not null,
    payload jsonb not null default '{}'::jsonb
);

create table if not exists policies (
    id text primary key,
    name text not null,
    body jsonb not null,
    status text not null default 'active',
    created_at timestamptz not null
);

create table if not exists agent_strategies (
    id text primary key,
    name text not null,
    body jsonb not null,
    status text not null default 'draft',
    created_at timestamptz not null
);

create table if not exists evaluation_runs (
    id text primary key,
    benchmark_name text not null,
    policy_version_id text not null,
    agent_strategy_id text not null,
    model_name text not null,
    status text not null,
    summary jsonb not null default '{}'::jsonb,
    items jsonb not null default '[]'::jsonb,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz not null
);

create table if not exists release_gates (
    id bigserial primary key,
    evaluation_run_id text not null references evaluation_runs(id),
    agent_strategy_id text not null,
    passed boolean not null,
    status text not null,
    checks jsonb not null default '{}'::jsonb,
    created_at timestamptz not null
);

create table if not exists trace_dataset_items (
    id text primary key,
    agent_run_id text not null references agent_runs(id),
    body jsonb not null,
    status text not null default 'candidate',
    created_at timestamptz not null
);

create table if not exists preference_pairs (
    id text primary key,
    chosen_run_id text not null references agent_runs(id),
    rejected_run_id text not null references agent_runs(id),
    task_id text,
    body jsonb not null,
    status text not null default 'candidate',
    created_at timestamptz not null
);

create table if not exists dataset_snapshots (
    id text primary key,
    version_name text not null,
    trace_item_ids jsonb not null default '[]'::jsonb,
    preference_pair_ids jsonb not null default '[]'::jsonb,
    item_count integer not null default 0,
    preference_pair_count integer not null default 0,
    filters jsonb not null default '{}'::jsonb,
    status text not null default 'ready',
    notes text,
    created_at timestamptz not null
);

create table if not exists memory_items (
    id text primary key,
    task_id text,
    agent_run_id text,
    scope text not null,
    memory_type text not null,
    key text not null,
    summary text not null,
    detail_json jsonb not null default '{}'::jsonb,
    status text not null default 'active',
    created_at timestamptz not null
);

create table if not exists approval_requests (
    id text primary key,
    run_id text not null references agent_runs(id),
    step_id text,
    tool_name text not null,
    input jsonb not null default '{}'::jsonb,
    status text not null default 'pending',
    reason text,
    decided_by text,
    created_at timestamptz not null,
    decided_at timestamptz
);

create table if not exists jobs (
    id text primary key,
    kind text not null,
    resource_id text not null,
    task_id text,
    status text not null default 'queued',
    attempts integer not null default 0,
    error_summary text,
    created_at timestamptz not null,
    updated_at timestamptz not null,
    started_at timestamptz,
    finished_at timestamptz
);

create table if not exists model_configs (
    id text primary key,
    provider text not null,
    model_name text not null,
    role text not null,
    context_window integer not null,
    cost_per_1k_tokens numeric(12, 6) not null,
    config jsonb not null default '{}'::jsonb,
    status text not null default 'active',
    created_at timestamptz not null
);

create table if not exists extension_manifests (
    id text primary key,
    type text not null,
    name text not null,
    description text,
    config jsonb not null default '{}'::jsonb,
    status text not null default 'enabled',
    created_at timestamptz not null
);

create table if not exists research_briefs (
    id text primary key,
    task_id text not null references tasks(id),
    question text not null,
    domain text not null,
    status text not null default 'completed',
    source_summary jsonb not null default '{}'::jsonb,
    papers jsonb not null default '[]'::jsonb,
    hypotheses jsonb not null default '[]'::jsonb,
    experiments jsonb not null default '[]'::jsonb,
    citations jsonb not null default '[]'::jsonb,
    report text,
    created_at timestamptz not null
);

create table if not exists notebook_runs (
    id text primary key,
    brief_id text not null references research_briefs(id),
    status text not null default 'completed',
    cells jsonb not null default '[]'::jsonb,
    metrics jsonb not null default '{}'::jsonb,
    created_at timestamptz not null
);

create table if not exists audit_logs (
    id text primary key,
    actor_id text,
    action text not null,
    resource_type text not null,
    resource_id text not null,
    decision text,
    detail_json jsonb not null default '{}'::jsonb,
    created_at timestamptz not null
);

create index if not exists idx_tasks_status on tasks(status);
create index if not exists idx_users_workspace on users(workspace_id);
create index if not exists idx_repo_connections_workspace on repository_connections(workspace_id);
create index if not exists idx_runs_task_id on agent_runs(task_id);
create index if not exists idx_steps_run_id on agent_steps(run_id);
create index if not exists idx_tool_calls_run_id on tool_calls(run_id);
create index if not exists idx_artifacts_run_id on artifacts(run_id);
create index if not exists idx_events_run_id on trace_events(run_id);
create index if not exists idx_audit_resource on audit_logs(resource_type, resource_id);
create index if not exists idx_memory_task on memory_items(task_id);
create index if not exists idx_approvals_status on approval_requests(status);
create index if not exists idx_jobs_status on jobs(status);
create index if not exists idx_models_role on model_configs(role);
create index if not exists idx_extensions_type on extension_manifests(type);
create index if not exists idx_research_domain on research_briefs(domain);
create index if not exists idx_dataset_snapshots_status on dataset_snapshots(status);
