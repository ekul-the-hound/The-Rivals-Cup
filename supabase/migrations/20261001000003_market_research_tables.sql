-- Public/authorized research data. Written only by server-side ingestion (service role) later.

create table public.market_bars (
  id uuid primary key default gen_random_uuid(),
  security_id uuid not null references public.securities (id) on delete cascade,
  bar_date date not null,
  timeframe text not null default '1D',
  open numeric, high numeric, low numeric, close numeric, adj_close numeric,
  volume bigint,
  vwap numeric,
  source text not null,
  data_status public.data_status not null default 'UNVERIFIED',
  retrieved_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (security_id, bar_date, timeframe, source)
);
create index on public.market_bars (security_id, bar_date desc);

create table public.daily_quotes (
  id uuid primary key default gen_random_uuid(),
  security_id uuid not null references public.securities (id) on delete cascade,
  quote_ts timestamptz not null,
  price numeric, bid numeric, ask numeric, volume bigint, previous_close numeric,
  source text not null,
  data_status public.data_status not null default 'UNVERIFIED',
  retrieved_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (security_id, quote_ts, source)
);
create index on public.daily_quotes (security_id, quote_ts desc);

create table public.liquidity_metrics (
  id uuid primary key default gen_random_uuid(),
  security_id uuid not null references public.securities (id) on delete cascade,
  as_of_date date not null,
  adv_20d_shares numeric,
  adv_20d_usd numeric,
  avg_spread_bps numeric,
  est_max_position_usd numeric,  -- ESTIMATE of WSR-tolerable size; never authoritative
  method text not null default 'adv_pct_v1',
  data_status public.data_status not null default 'UNVERIFIED',
  evidence_quality public.evidence_quality not null default 'DERIVED',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (security_id, as_of_date, method)
);

create table public.market_context_snapshots (
  id uuid primary key default gen_random_uuid(),
  snapshot_at timestamptz not null,
  spy_return_1d numeric, qqq_return_1d numeric, iwm_return_1d numeric,
  sector_returns jsonb not null default '{}'::jsonb,
  breadth jsonb not null default '{}'::jsonb,
  payload jsonb not null default '{}'::jsonb,
  source text,
  data_status public.data_status not null default 'UNVERIFIED',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on public.market_context_snapshots (snapshot_at desc);

create table public.macro_context_snapshots (
  id uuid primary key default gen_random_uuid(),
  as_of_date date not null,
  rates jsonb not null default '{}'::jsonb,
  upcoming_events jsonb not null default '[]'::jsonb,
  summary text,
  source text,
  data_status public.data_status not null default 'UNVERIFIED',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on public.macro_context_snapshots (as_of_date desc);

create table public.filing_documents (
  id uuid primary key default gen_random_uuid(),
  security_id uuid not null references public.securities (id) on delete cascade,
  form_type text not null,
  accession_number text not null unique,
  filed_at timestamptz,
  period_of_report date,
  url text,
  title text,
  summary text,
  evidence_quality public.evidence_quality not null default 'PRIMARY',
  data_status public.data_status not null default 'UNVERIFIED',
  retrieved_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on public.filing_documents (security_id, filed_at desc);

create table public.corporate_catalysts (
  id uuid primary key default gen_random_uuid(),
  security_id uuid not null references public.securities (id) on delete cascade,
  catalyst_type public.catalyst_type not null default 'OTHER',
  headline text not null,
  detail text,
  event_date date,
  source_url text,
  filing_id uuid references public.filing_documents (id) on delete set null,
  evidence_quality public.evidence_quality not null default 'UNVERIFIED',
  data_status public.data_status not null default 'UNVERIFIED',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on public.corporate_catalysts (security_id, event_date desc);

create table public.news_items (
  id uuid primary key default gen_random_uuid(),
  security_id uuid references public.securities (id) on delete set null,
  headline text not null,
  source_name text,
  url text,
  published_at timestamptz,
  summary text,
  content_hash text not null unique,
  evidence_quality public.evidence_quality not null default 'SECONDARY',
  data_status public.data_status not null default 'UNVERIFIED',
  retrieved_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on public.news_items (security_id, published_at desc);

create table public.research_packets (
  id uuid primary key default gen_random_uuid(),
  pair_id uuid not null references public.peer_pairs (id) on delete cascade,
  packet_version integer not null default 1,
  as_of timestamptz not null default now(),
  status public.review_status not null default 'DRAFT',
  content jsonb not null default '{}'::jsonb,
  content_hash text,
  data_status public.data_status not null default 'UNVERIFIED',
  evidence_quality public.evidence_quality not null default 'DERIVED',
  generated_by text not null default 'system',
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (pair_id, packet_version)
);

create table public.pair_rankings (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null,
  pair_id uuid not null references public.peer_pairs (id) on delete cascade,
  ranked_at timestamptz not null default now(),
  rank integer not null,
  score numeric,
  direction_hint public.direction not null default 'NO_TRADE',  -- research hint only, never a signal
  correlation_60d numeric,
  spread_zscore numeric,
  liquidity_ok boolean,
  components jsonb not null default '{}'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  data_status public.data_status not null default 'UNVERIFIED',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (run_id, pair_id)
);
create index on public.pair_rankings (ranked_at desc);

create table public.pair_research_reviews (
  id uuid primary key default gen_random_uuid(),
  pair_id uuid not null references public.peer_pairs (id) on delete cascade,
  packet_id uuid references public.research_packets (id) on delete set null,
  status public.review_status not null default 'DRAFT',
  direction public.direction not null default 'NO_TRADE',  -- human research view, not an order
  thesis text,
  risks text,
  checklist jsonb not null default '[]'::jsonb,
  claude_review_notes text,
  owner_notes text,
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
