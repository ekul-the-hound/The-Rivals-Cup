-- updated_at triggers, RLS (enabled + forced) on every table, least-privilege grants,
-- and a NOLOGIN read-only role reserved for the future MCP endpoint.

do $$
declare t text;
begin
  foreach t in array array[
    'profiles','securities','companies','sector_etf_mappings','peer_pairs','peer_pair_members',
    'market_bars','daily_quotes','liquidity_metrics','market_context_snapshots','macro_context_snapshots',
    'filing_documents','corporate_catalysts','news_items','research_packets','pair_rankings',
    'pair_research_reviews','manual_portfolios','manual_positions','manual_trades',
    'portfolio_daily_values','score_snapshots','data_quality_issues','provider_run_logs',
    'mcp_audit_logs','system_control_state'
  ] loop
    execute format('create trigger set_updated_at before update on public.%I for each row execute function public.set_updated_at()', t);
    execute format('alter table public.%I enable row level security', t);
    execute format('alter table public.%I force row level security', t);
    execute format('create policy owner_select on public.%I for select to authenticated using (public.is_owner())', t);
  end loop;
end $$;

-- Owner write policies (manual-entry / editable-mapping tables only).
do $$
declare t text;
begin
  foreach t in array array[
    'securities','companies','sector_etf_mappings','peer_pairs','peer_pair_members',
    'pair_research_reviews','manual_portfolios','manual_positions','manual_trades','portfolio_daily_values'
  ] loop
    execute format('create policy owner_insert on public.%I for insert to authenticated with check (public.is_owner())', t);
    execute format('create policy owner_update on public.%I for update to authenticated using (public.is_owner()) with check (public.is_owner())', t);
    execute format('create policy owner_delete on public.%I for delete to authenticated using (public.is_owner())', t);
  end loop;
end $$;
create policy owner_update on public.data_quality_issues for update to authenticated
  using (public.is_owner()) with check (public.is_owner());
create policy owner_update on public.system_control_state for update to authenticated
  using (public.is_owner()) with check (public.is_owner());

-- Privileges: anon gets nothing; authenticated gets SELECT plus writes only where policies exist.
revoke all on all tables in schema public from anon, authenticated;
alter default privileges in schema public revoke all on tables from anon, authenticated;
grant select on all tables in schema public to authenticated;
grant insert, update, delete on
  public.securities, public.companies, public.sector_etf_mappings, public.peer_pairs,
  public.peer_pair_members, public.pair_research_reviews, public.manual_portfolios,
  public.manual_positions, public.manual_trades, public.portfolio_daily_values
  to authenticated;
grant update on public.data_quality_issues, public.system_control_state to authenticated;
revoke execute on function public.is_owner() from public, anon;
grant execute on function public.is_owner() to authenticated;
-- Ingestion/ranking/packet/audit tables are written only by service_role (bypasses RLS).

-- Read-only role for the future single MCP endpoint (DB-enforced, not just app-enforced).
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'mcp_readonly') then
    create role mcp_readonly nologin noinherit;
  end if;
end $$;
alter role mcp_readonly set default_transaction_read_only = on;
grant usage on schema public to mcp_readonly;
do $$
declare t text;
begin
  foreach t in array array[
    'securities','companies','sector_etf_mappings','peer_pairs','peer_pair_members',
    'market_bars','daily_quotes','liquidity_metrics','market_context_snapshots','macro_context_snapshots',
    'filing_documents','corporate_catalysts','news_items','research_packets','pair_rankings',
    'pair_research_reviews','manual_portfolios','manual_positions','manual_trades',
    'portfolio_daily_values','score_snapshots','data_quality_issues','system_control_state'
  ] loop
    execute format('grant select on public.%I to mcp_readonly', t);
    execute format('create policy mcp_readonly_select on public.%I for select to mcp_readonly using (true)', t);
  end loop;
end $$;
-- NOTE: no INSERT/UPDATE/DELETE is granted to mcp_readonly. profiles, provider_run_logs and
-- mcp_audit_logs are intentionally not readable by it.
