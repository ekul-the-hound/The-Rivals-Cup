-- Enums + helpers. Research-only system: no order/execution concepts exist.
create extension if not exists pgcrypto;

create type public.direction as enum ('LONG', 'SHORT', 'NO_TRADE');
create type public.pair_status as enum ('CANDIDATE', 'APPROVED_FOR_REVIEW', 'ACTIVE_MANUALLY', 'CLOSED_MANUALLY', 'REJECTED');
create type public.data_status as enum ('AVAILABLE', 'STALE', 'MISSING', 'UNVERIFIED', 'MANUAL');
create type public.evidence_quality as enum ('PRIMARY', 'SECONDARY', 'DERIVED', 'UNVERIFIED');
create type public.catalyst_type as enum ('M_AND_A', 'BUYBACK', 'OFFERING', 'MANAGEMENT_CHANGE', 'MATERIAL_AGREEMENT', 'REGULATORY', 'INSIDER', 'OTHER');
create type public.review_status as enum ('DRAFT', 'READY_FOR_CLAUDE_REVIEW', 'REVIEWED', 'REJECTED');
create type public.system_mode as enum ('RESEARCH_ONLY', 'PAUSED');

create or replace function public.set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;
