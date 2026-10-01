create table public.data_quality_issues (
  id uuid primary key default gen_random_uuid(),
  entity_table text,
  entity_id uuid,
  security_id uuid references public.securities (id) on delete set null,
  severity text not null default 'WARNING' check (severity in ('INFO', 'WARNING', 'ERROR')),
  issue_type text not null,
  description text,
  data_status public.data_status not null default 'UNVERIFIED',
  detected_at timestamptz not null default now(),
  resolved_at timestamptz,
  resolution_notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on public.data_quality_issues (resolved_at, severity);

create table public.provider_run_logs (
  id uuid primary key default gen_random_uuid(),
  provider text not null,
  job_name text not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null default 'RUNNING' check (status in ('RUNNING', 'SUCCEEDED', 'FAILED', 'PARTIAL')),
  rows_read integer,
  rows_written integer,
  error_message text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on public.provider_run_logs (started_at desc);

-- Written server-side by the (future) MCP service as an access log, not by MCP tools.
create table public.mcp_audit_logs (
  id uuid primary key default gen_random_uuid(),
  occurred_at timestamptz not null default now(),
  request_id text,
  client_id text,
  tool_name text not null,
  args jsonb not null default '{}'::jsonb,   -- redacted before write
  result_summary jsonb not null default '{}'::jsonb,
  status text not null default 'OK',
  duration_ms integer,
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on public.mcp_audit_logs (occurred_at desc);

create table public.system_control_state (
  id smallint primary key default 1 check (id = 1),  -- singleton
  mode public.system_mode not null default 'PAUSED',
  -- Hard-locked: there is no signal sending feature. The CHECK makes `true` unrepresentable.
  signal_sending_enabled boolean not null default false check (signal_sending_enabled = false),
  provider_ingestion_enabled boolean not null default false,
  reason text,
  updated_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
insert into public.system_control_state (id, mode, reason)
values (1, 'PAUSED', 'initial default') on conflict (id) do nothing;
