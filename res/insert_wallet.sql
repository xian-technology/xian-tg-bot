INSERT INTO wallets (user_id, address, privkey)
VALUES (?, ?, ?)
ON CONFLICT(user_id) DO NOTHING
