DELETE FROM sell_transactions
WHERE id = ? AND user_id = ?
RETURNING *