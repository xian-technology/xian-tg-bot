CREATE TABLE tokens (
    user_id INTEGER NOT NULL,
    contract TEXT NOT NULL,
    ticker TEXT NOT NULL,
    decimals INTEGER DEFAULT 4,
    PRIMARY KEY (user_id, contract)
)
