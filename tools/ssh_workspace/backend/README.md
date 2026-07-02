# SSH Workspace Backend

`ssh_workspace` is a personal SSH tool. All rows are scoped by `owner_user_id`; there is no admin sharing model in this tool.

## Files

- `router.py`: FastAPI request models, REST endpoints, and the terminal WebSocket route.
- `service.py`: database schema, encrypted credential storage, SSH helpers, screen operations, command history, templates, scheduled tasks, and WebSocket terminal bridging.
- `scheduler.py`: registers the due-task collector with the platform scheduler.

## Database Tables

- `ssh_servers`: personal SSH servers, auth metadata, encrypted credentials, and screen capability status.
- `ssh_command_history`: command memory written by the UI, template executions, and scheduled runs.
- `ssh_command_templates`: saved commands and variable templates.
- `ssh_screen_sessions`: screen sessions created or refreshed by the platform.
- `ssh_scheduled_tasks`: platform-side schedules that launch commands in remote screen sessions.
- `ssh_task_runs`: per-trigger history for scheduled tasks.

## Screen Contract

Direct interactive terminals work without `screen`. Background session retention and scheduled command launch require the remote server to have `screen`; service functions call `_require_screen` before those operations and return `SCREEN_UNAVAILABLE` when it is not available.

Scheduled tasks are not installed into remote cron. The platform scheduler checks due tasks every 30 seconds and starts each due command with `screen -dmS ... bash -lc ...`, then records a run row.
