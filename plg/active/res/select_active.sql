SELECT user_id, user_name
FROM (
  -- One row per user: SQLite takes bare columns from the MAX(date_time) row,
  -- so a renamed user is paid once under the most recent name.
  SELECT user_id, user_name, MAX(date_time) AS last_active
  FROM active
  WHERE group_id = ?
    AND date_time > ?
    AND msg_text NOT LIKE '/%'
  GROUP BY user_id
)
ORDER BY last_active DESC