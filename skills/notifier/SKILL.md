---
name: notifier
description: Send proactive Feishu notifications after task completion. Use this skill whenever a task runs longer than 10 minutes and then finishes, or whenever the user explicitly asks to be notified after the task completes. First version supports Feishu text messages through the bundled Python script.
metadata:
  {
    "openclaw":
      {
        "emoji": "🔔",
        "requires": { "bins": ["python3"] },
      },
  }
---

# Notifier

Use this skill when an agent run, subagent run, or external automation event should actively notify a human operator.

Automatically use it in either of these cases:

- The task ran for more than 10 minutes and has now finished.
- The user explicitly asked to be notified after the task completes.

This skill does **not** register runtime hooks by itself. Instead, wire your host runtime or event system to call the bundled script when a target event fires, for example `agent_end`, `subagent_ended`, or a custom task-complete callback.

## Config sources

The script loads Feishu settings in this order:

1. CLI flags such as `--app-id`, `--app-secret`, `--to`, `--domain`
2. Environment variables
3. A JSON config file passed by `--config` or pointed to by `FEISHU_NOTIFY_CONFIG`

If both environment variables and config file are missing the required settings, stop and ask the user to provide the Feishu app id, app secret, and target before attempting to send.

## Environment variables

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_NOTIFY_TO` target, for example `chat:oc_xxx`, `user:ou_xxx`, `open_id:ou_xxx`, or `email:name@example.com`

Optional:

- `FEISHU_DOMAIN` = `feishu`, `lark`, or a custom base URL
- `FEISHU_NOTIFY_CONFIG` = path to a JSON config file

## Config file format

Pass a JSON file with either flat keys or a nested `feishu`/`notifier` object. Supported keys include:

- `appId` or `app_id`
- `appSecret` or `app_secret`
- `notifyTo`, `target`, or `to`
- `domain`

Example:

```json
{
  "feishu": {
    "appId": "cli_a1b2c3",
    "appSecret": "secret_value",
    "notifyTo": "chat:oc_xxx",
    "domain": "feishu"
  }
}
```

## Quick start

Send a direct completion notification:

```bash
python3 {baseDir}/scripts/feishu_notify.py \
  --event agent_end \
  --status success \
  --task "Refactor auth module" \
  --summary "All targeted tests passed"
```

Load config from a specified file:

```bash
python3 {baseDir}/scripts/feishu_notify.py \
  --config /path/to/notifier.json \
  --event agent_end \
  --status success \
  --task "Refactor auth module"
```

Send a fully custom message:

```bash
python3 {baseDir}/scripts/feishu_notify.py \
  --message "Agent finished waiting for your next instruction."
```

Pipe a structured event payload from stdin:

```bash
printf '%s\n' '{"event":"agent_end","success":true,"agentId":"claude-1","task":"Review PR #42","summary":"Found 2 issues","durationMs":18420}' \
  | python3 {baseDir}/scripts/feishu_notify.py --payload -
```

Preview without sending:

```bash
python3 {baseDir}/scripts/feishu_notify.py \
  --dry-run \
  --event agent_end \
  --status success \
  --task "Implement notifier skill"
```

## Supported target formats

- `chat:<chat_id>` or `group:<chat_id>`
- `user:<user_id>`
- `open_id:<open_id>`
- `union_id:<union_id>`
- `email:<email>`

If no prefix is provided, the script defaults to `chat_id`.

## Recommended wiring

- Use your runtime's completion event to call the script once the task reaches a terminal state.
- Pass explicit `--task`, `--summary`, and `--status` flags when available.
- If the runtime already emits JSON, pipe it with `--payload -` and let the script format a compact Feishu message.
- Use `--message` when you need full control over the final text.
- When onboarding the skill, first look for environment variables or a `--config` / `FEISHU_NOTIFY_CONFIG` file. If neither yields the required fields, ask the user for the missing configuration instead of guessing.

## Notes

- Current implementation sends Feishu **text** messages only.
- The script uses tenant access token flow compatible with Feishu/Lark app credentials.
- You can find notifier.json under the notifier skill directory.
