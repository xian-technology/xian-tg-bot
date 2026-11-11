DELETE FROM buy_transactions
WHERE id = ? AND user_id = ?
RETURNING *