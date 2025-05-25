CREATE TABLE sell_transactions
(
  id            TEXT PRIMARY KEY,
  user_id       INTEGER,
  sell_contract TEXT,
  sell_symbol   TEXT,
  buy_contract  TEXT,
  buy_symbol    TEXT,
  amount        REAL
)