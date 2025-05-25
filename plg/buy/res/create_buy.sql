CREATE TABLE buy_transactions
(
  id            TEXT PRIMARY KEY,
  user_id       INTEGER,
  buy_contract  TEXT,
  buy_symbol    TEXT,
  sell_contract TEXT,
  sell_symbol   TEXT,
  amount        REAL
)