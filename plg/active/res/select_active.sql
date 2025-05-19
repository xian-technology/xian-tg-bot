SELECT DISTINCT user_id, user_name
FROM active
WHERE group_id = ?
  AND date_time > ?
  AND msg_text NOT LIKE '/%'
ORDER BY date_time DESC