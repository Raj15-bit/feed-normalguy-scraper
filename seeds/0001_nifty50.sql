-- Nifty 50 companies seed. Paste into Supabase SQL Editor → Run.
-- Idempotent: ON CONFLICT (slug) DO UPDATE keeps bse_code / nse_symbol fresh
-- without disturbing manually-edited rows (market_cap etc).
--
-- Sectors must exist first — they're inserted by the demo schema migration
-- (0000_base_schema_demo.sql in the feed-normalguy repo) which already creates
-- it/banking/oil-gas/fmcg/auto/pharma. We add the rest here.

insert into public.sectors (slug, name) values
  ('it', 'Information Technology'),
  ('banking', 'Banking'),
  ('oil-gas', 'Oil & Gas'),
  ('fmcg', 'FMCG'),
  ('auto', 'Auto'),
  ('pharma', 'Pharma'),
  ('financial-services', 'Financial Services'),
  ('metals', 'Metals & Mining'),
  ('cement', 'Cement'),
  ('power', 'Power'),
  ('telecom', 'Telecom'),
  ('consumer-durables', 'Consumer Durables'),
  ('paints', 'Paints'),
  ('insurance', 'Insurance'),
  ('infrastructure', 'Infrastructure'),
  ('conglomerate', 'Conglomerate'),
  ('media', 'Media')
on conflict (slug) do nothing;

insert into public.companies (slug, name, nse_symbol, bse_code, sector_slug) values
  ('reliance',     'Reliance Industries',         'RELIANCE',   '500325', 'oil-gas'),
  ('tcs',          'Tata Consultancy Services',   'TCS',        '532540', 'it'),
  ('hdfcbank',     'HDFC Bank',                   'HDFCBANK',   '500180', 'banking'),
  ('icicibank',    'ICICI Bank',                  'ICICIBANK',  '532174', 'banking'),
  ('hindunilvr',   'Hindustan Unilever',          'HINDUNILVR', '500696', 'fmcg'),
  ('infy',         'Infosys',                     'INFY',       '500209', 'it'),
  ('sbin',         'State Bank of India',         'SBIN',       '500112', 'banking'),
  ('bhartiartl',   'Bharti Airtel',               'BHARTIARTL', '532454', 'telecom'),
  ('itc',          'ITC',                         'ITC',        '500875', 'fmcg'),
  ('kotakbank',    'Kotak Mahindra Bank',         'KOTAKBANK',  '500247', 'banking'),
  ('lt',           'Larsen & Toubro',             'LT',         '500510', 'infrastructure'),
  ('axisbank',     'Axis Bank',                   'AXISBANK',   '532215', 'banking'),
  ('asianpaint',   'Asian Paints',                'ASIANPAINT', '500820', 'paints'),
  ('maruti',       'Maruti Suzuki',               'MARUTI',     '532500', 'auto'),
  ('hcltech',      'HCL Technologies',            'HCLTECH',    '532281', 'it'),
  ('sunpharma',    'Sun Pharmaceutical',          'SUNPHARMA',  '524715', 'pharma'),
  ('titan',        'Titan Company',               'TITAN',      '500114', 'consumer-durables'),
  ('m-and-m',      'Mahindra & Mahindra',         'M&M',        '500520', 'auto'),
  ('bajfinance',   'Bajaj Finance',               'BAJFINANCE', '500034', 'financial-services'),
  ('ultracemco',   'UltraTech Cement',            'ULTRACEMCO', '532538', 'cement'),
  ('wipro',        'Wipro',                       'WIPRO',      '507685', 'it'),
  ('nestleind',    'Nestle India',                'NESTLEIND',  '500790', 'fmcg'),
  ('ntpc',         'NTPC',                        'NTPC',       '532555', 'power'),
  ('powergrid',    'Power Grid Corporation',      'POWERGRID',  '532898', 'power'),
  ('jswsteel',     'JSW Steel',                   'JSWSTEEL',   '500228', 'metals'),
  ('tatamotors',   'Tata Motors',                 'TATAMOTORS', '500570', 'auto'),
  ('tatasteel',    'Tata Steel',                  'TATASTEEL',  '500470', 'metals'),
  ('coalindia',    'Coal India',                  'COALINDIA',  '533278', 'metals'),
  ('hindalco',     'Hindalco Industries',         'HINDALCO',   '500440', 'metals'),
  ('grasim',       'Grasim Industries',           'GRASIM',     '500300', 'cement'),
  ('techm',        'Tech Mahindra',               'TECHM',      '532755', 'it'),
  ('bajajfinsv',   'Bajaj Finserv',               'BAJAJFINSV', '532978', 'financial-services'),
  ('drreddy',      'Dr. Reddy''s Laboratories',   'DRREDDY',    '500124', 'pharma'),
  ('eichermot',    'Eicher Motors',               'EICHERMOT',  '505200', 'auto'),
  ('cipla',        'Cipla',                       'CIPLA',      '500087', 'pharma'),
  ('apollohosp',   'Apollo Hospitals',            'APOLLOHOSP', '508869', 'pharma'),
  ('britannia',    'Britannia Industries',        'BRITANNIA',  '500825', 'fmcg'),
  ('divislab',     'Divi''s Laboratories',        'DIVISLAB',   '532488', 'pharma'),
  ('heromotoco',   'Hero MotoCorp',               'HEROMOTOCO', '500182', 'auto'),
  ('indusindbk',   'IndusInd Bank',               'INDUSINDBK', '532187', 'banking'),
  ('bpcl',         'Bharat Petroleum',            'BPCL',       '500547', 'oil-gas'),
  ('ongc',         'Oil & Natural Gas Corp',      'ONGC',       '500312', 'oil-gas'),
  ('adaniports',   'Adani Ports & SEZ',           'ADANIPORTS', '532921', 'infrastructure'),
  ('adanient',     'Adani Enterprises',           'ADANIENT',   '512599', 'conglomerate'),
  ('bajaj-auto',   'Bajaj Auto',                  'BAJAJ-AUTO', '532977', 'auto'),
  ('sbilife',      'SBI Life Insurance',          'SBILIFE',    '540719', 'insurance'),
  ('hdfclife',     'HDFC Life Insurance',         'HDFCLIFE',   '540777', 'insurance'),
  ('shriramfin',   'Shriram Finance',             'SHRIRAMFIN', '511218', 'financial-services'),
  ('trent',        'Trent',                       'TRENT',      '500251', 'consumer-durables'),
  ('tatacons',     'Tata Consumer Products',      'TATACONSUM', '500800', 'fmcg')
on conflict (slug) do update set
  nse_symbol = excluded.nse_symbol,
  bse_code   = excluded.bse_code,
  sector_slug = excluded.sector_slug;
