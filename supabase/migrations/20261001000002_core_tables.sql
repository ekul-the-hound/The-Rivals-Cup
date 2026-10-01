-- Core reference tables: profiles, securities, companies, mappings, peer pairs.

create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  email text,
  display_name text,
  is_owner boolean not null default false,
  timezone text not null default 'America/Chicago',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
-- Single-owner enforcement: at most one owner row.
create unique index profiles_single_owner on public.profiles (is_owner) where is_owner;

create or replace function public.is_owner() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.profiles p where p.id = auth.uid() and p.is_owner)
$$;

-- First account created becomes the owner; any later account is NOT an owner.
-- (Also disable public sign-ups in the Supabase dashboard after creating the owner.)
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email, is_owner)
  values (new.id, new.email, not exists (select 1 from public.profiles where is_owner));
  return new;
end $$;
revoke all on function public.handle_new_user() from public, anon, authenticated;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

create table public.securities (
  id uuid primary key default gen_random_uuid(),
  ticker text not null unique,
  name text,
  security_type text not null default 'EQUITY' check (security_type in ('EQUITY', 'ETF', 'INDEX')),
  exchange text,
  currency text not null default 'USD',
  sector text,
  industry text,
  is_etf boolean not null default false,
  is_benchmark boolean not null default false,
  is_active boolean not null default true,
  wsr_eligibility public.data_status not null default 'UNVERIFIED',
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.companies (
  id uuid primary key default gen_random_uuid(),
  security_id uuid not null unique references public.securities (id) on delete cascade,
  cik text,
  legal_name text,
  description text,
  sic_code text,
  fiscal_year_end text,
  market_cap_usd numeric,
  shares_outstanding numeric,
  market_cap_as_of date,
  data_status public.data_status not null default 'UNVERIFIED',
  source text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.sector_etf_mappings (
  id uuid primary key default gen_random_uuid(),
  sector text not null,
  etf_security_id uuid not null references public.securities (id) on delete cascade,
  is_primary boolean not null default true,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (sector, etf_security_id)
);

create table public.peer_pairs (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  sector text,
  rationale text,
  status public.pair_status not null default 'CANDIDATE',
  created_by text not null default 'manual',
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.peer_pair_members (
  id uuid primary key default gen_random_uuid(),
  pair_id uuid not null references public.peer_pairs (id) on delete cascade,
  security_id uuid not null references public.securities (id) on delete restrict,
  role text not null check (role in ('A', 'B')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (pair_id, role),
  unique (pair_id, security_id)
);
create index on public.peer_pair_members (security_id);
