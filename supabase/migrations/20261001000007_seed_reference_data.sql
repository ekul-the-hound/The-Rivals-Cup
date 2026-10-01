-- Seed: benchmarks, sector ETFs, and a SMALL SAMPLE peer-pair universe.
-- Sample pairs are illustrative research candidates, NOT recommendations. WSR eligibility is
-- UNVERIFIED for every security and must be checked manually. All rows are editable.

insert into public.securities (ticker, name, security_type, exchange, sector, is_etf, is_benchmark) values
  ('SPY','SPDR S&P 500 ETF Trust','ETF','NYSE Arca',null,true,true),
  ('QQQ','Invesco QQQ Trust','ETF','NASDAQ',null,true,true),
  ('IWM','iShares Russell 2000 ETF','ETF','NYSE Arca',null,true,true),
  ('XLB','Materials Select Sector SPDR Fund','ETF','NYSE Arca','Materials',true,false),
  ('XLC','Communication Services Select Sector SPDR Fund','ETF','NYSE Arca','Communication Services',true,false),
  ('XLE','Energy Select Sector SPDR Fund','ETF','NYSE Arca','Energy',true,false),
  ('XLF','Financial Select Sector SPDR Fund','ETF','NYSE Arca','Financials',true,false),
  ('XLI','Industrial Select Sector SPDR Fund','ETF','NYSE Arca','Industrials',true,false),
  ('XLK','Technology Select Sector SPDR Fund','ETF','NYSE Arca','Information Technology',true,false),
  ('XLP','Consumer Staples Select Sector SPDR Fund','ETF','NYSE Arca','Consumer Staples',true,false),
  ('XLRE','Real Estate Select Sector SPDR Fund','ETF','NYSE Arca','Real Estate',true,false),
  ('XLU','Utilities Select Sector SPDR Fund','ETF','NYSE Arca','Utilities',true,false),
  ('XLV','Health Care Select Sector SPDR Fund','ETF','NYSE Arca','Health Care',true,false),
  ('XLY','Consumer Discretionary Select Sector SPDR Fund','ETF','NYSE Arca','Consumer Discretionary',true,false)
on conflict (ticker) do nothing;

insert into public.securities (ticker, name, security_type, exchange, sector, is_etf) values
  ('KO','Coca-Cola Co','EQUITY','NYSE','Consumer Staples',false),
  ('PEP','PepsiCo Inc','EQUITY','NASDAQ','Consumer Staples',false),
  ('HD','Home Depot Inc','EQUITY','NYSE','Consumer Discretionary',false),
  ('LOW','Lowe''s Companies Inc','EQUITY','NYSE','Consumer Discretionary',false),
  ('V','Visa Inc','EQUITY','NYSE','Financials',false),
  ('MA','Mastercard Inc','EQUITY','NYSE','Financials',false),
  ('XOM','Exxon Mobil Corp','EQUITY','NYSE','Energy',false),
  ('CVX','Chevron Corp','EQUITY','NYSE','Energy',false),
  ('JPM','JPMorgan Chase & Co','EQUITY','NYSE','Financials',false),
  ('BAC','Bank of America Corp','EQUITY','NYSE','Financials',false),
  ('UPS','United Parcel Service Inc','EQUITY','NYSE','Industrials',false),
  ('FDX','FedEx Corp','EQUITY','NYSE','Industrials',false),
  ('MRK','Merck & Co Inc','EQUITY','NYSE','Health Care',false),
  ('PFE','Pfizer Inc','EQUITY','NYSE','Health Care',false),
  ('AMD','Advanced Micro Devices Inc','EQUITY','NASDAQ','Information Technology',false),
  ('INTC','Intel Corp','EQUITY','NASDAQ','Information Technology',false)
on conflict (ticker) do nothing;

insert into public.sector_etf_mappings (sector, etf_security_id, is_primary)
select v.sector, s.id, true
from (values
  ('Materials','XLB'),('Communication Services','XLC'),('Energy','XLE'),('Financials','XLF'),
  ('Industrials','XLI'),('Information Technology','XLK'),('Consumer Staples','XLP'),
  ('Real Estate','XLRE'),('Utilities','XLU'),('Health Care','XLV'),('Consumer Discretionary','XLY')
) as v(sector, ticker)
join public.securities s on s.ticker = v.ticker
on conflict (sector, etf_security_id) do nothing;

insert into public.peer_pairs (name, sector, rationale, status) values
  ('KO / PEP','Consumer Staples','Large-cap beverage peers (sample)','CANDIDATE'),
  ('HD / LOW','Consumer Discretionary','Home-improvement retail duopoly (sample)','CANDIDATE'),
  ('V / MA','Financials','Payment network peers (sample)','CANDIDATE'),
  ('XOM / CVX','Energy','Integrated oil majors (sample)','CANDIDATE'),
  ('JPM / BAC','Financials','Money-center banks (sample)','CANDIDATE'),
  ('UPS / FDX','Industrials','Parcel delivery peers (sample)','CANDIDATE'),
  ('MRK / PFE','Health Care','Large-cap pharma peers (sample)','CANDIDATE'),
  ('AMD / INTC','Information Technology','x86 CPU peers (sample)','CANDIDATE')
on conflict (name) do nothing;

insert into public.peer_pair_members (pair_id, security_id, role)
select p.id, s.id, v.role
from (values
  ('KO / PEP','KO','A'),('KO / PEP','PEP','B'),('HD / LOW','HD','A'),('HD / LOW','LOW','B'),
  ('V / MA','V','A'),('V / MA','MA','B'),('XOM / CVX','XOM','A'),('XOM / CVX','CVX','B'),
  ('JPM / BAC','JPM','A'),('JPM / BAC','BAC','B'),('UPS / FDX','UPS','A'),('UPS / FDX','FDX','B'),
  ('MRK / PFE','MRK','A'),('MRK / PFE','PFE','B'),('AMD / INTC','AMD','A'),('AMD / INTC','INTC','B')
) as v(pair_name, ticker, role)
join public.peer_pairs p on p.name = v.pair_name
join public.securities s on s.ticker = v.ticker
on conflict do nothing;
