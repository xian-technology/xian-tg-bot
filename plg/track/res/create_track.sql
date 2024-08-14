CREATE TABLE active (
    group_id INTEGER,
    group_type TEXT,
    group_title TEXT,
    group_link TEXT,
    user_id INTEGER,
    user_type TEXT,
    user_first_name TEXT,
    user_last_name TEXT,
    user_username TEXT,
    msg_id INTEGER,
    msg_type TEXT,
    msg_text TEXT,
	date_time DATETIME DEFAULT CURRENT_TIMESTAMP
)