# PM2 daemon retention (Linux host example)

The application already bounds its own `log/bot.log` and archives. This template
covers only PM2's separate daemon `pm2.log`, which can grow during restart loops.
It is intended for a **dedicated bot service account and PM2 home**, not a shared
PM2 daemon managing unrelated services. No host configuration is applied by the
repository or bot startup.

Render `pm2-daemon.logrotate.in` after confirming the service account and PM2
home, for example:

```text
@SERVICE_USER@  -> xian-bot
@SERVICE_GROUP@ -> xian-bot
@PM2_HOME@      -> /home/xian-bot/.pm2
```

The rendered stanza must contain one absolute quoted `pm2.log` path, no wildcard,
and no remaining placeholders. Have the host administrator install it as
`/etc/xian-tg-bot/pm2-daemon.logrotate`, owned by root and not writable by the bot.
Use a dedicated state file so another logrotate invocation does not race this
policy. Verify the rendered file without changing logs:

```sh
sudo /usr/sbin/logrotate --debug --state /var/lib/logrotate/xian-tg-bot-pm2.status /etc/xian-tg-bot/pm2-daemon.logrotate
```

After review, schedule the same command **without `--debug`** every minute in a
root-managed cron entry or equivalent systemd timer. Do not also include this
stanza in another logrotate job with a different state file. This task does not
install or execute that schedule; executable locations may differ by host.

The policy rotates daily or when a scheduled check sees more than **50 MiB**,
retains at most **four archives plus the active file**, and removes rotated logs
older than **seven days** when rotation runs. The count can shorten history below
seven days, even at low volume. `ifempty` allows daily housekeeping during idle
periods instead of leaving old archives indefinitely. If the daemon log itself
has been removed, `missingok` skips it; removing an entire service also requires
the host owner to remove its retained archives.

The nominal budget is **250 MiB** for five 50 MiB files, but this is **not a hard
quota**: growth between checks can exceed the threshold, copy/truncate temporarily
needs extra disk space, and writes during that small copy/truncate window may be
lost. Monitor host disk usage and rotation failures. No application-wide PM2
module installation or global PM2 reconfiguration is required.

The options follow the [official logrotate manual](https://github.com/logrotate/logrotate/blob/main/logrotate.8.in)
and [PM2's daemon-log rotation example](https://pm2.keymetrics.io/docs/usage/log-management/#setting-up-a-native-logrotate).
The PM2 generated weekly/12-archive example is intentionally not used. The local
macOS development environment has no `logrotate` executable; this template's
syntax was checked against the upstream manual, but the rendered deployment must
be dry-run validated with the target host's installed version before scheduling.
