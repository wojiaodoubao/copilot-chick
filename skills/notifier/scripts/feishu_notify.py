#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


class NotifyError(RuntimeError):
    pass


@dataclass
class FeishuConfig:
    app_id: str
    app_secret: str
    target: str
    domain: str


@dataclass
class ConfigSources:
    config_path: Optional[str]
    config_data: dict[str, Any]


def first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
            continue
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, dict):
            text = compact_json(value)
            if text:
                return text
        if isinstance(value, list):
            if not value:
                continue
            items = []
            for item in value[:5]:
                if isinstance(item, str):
                    items.append(item.strip())
                else:
                    items.append(compact_json(item))
            text = "; ".join(filter(None, items)).strip()
            if text:
                return text
    return None


def compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return str(value)


def truncate(text: Optional[str], limit: int = 280) -> Optional[str]:
    if text is None:
        return None
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def normalize_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def resolve_api_base(domain: Optional[str]) -> str:
    normalized = (domain or "feishu").strip()
    if not normalized or normalized == "feishu":
        return "https://open.feishu.cn/open-apis"
    if normalized == "lark":
        return "https://open.larksuite.com/open-apis"
    if normalized.startswith("http://") or normalized.startswith("https://"):
        return normalized.rstrip("/") + "/open-apis"
    raise NotifyError("FEISHU_DOMAIN must be 'feishu', 'lark', or an absolute http(s) URL")


def parse_target(raw_target: str) -> tuple[str, str]:
    target = raw_target.strip()
    if not target:
        raise NotifyError("Missing Feishu target")
    target = target.replace("feishu:", "", 1).replace("lark:", "", 1)
    if ":" not in target:
        return target, "chat_id"
    prefix, value = target.split(":", 1)
    receive_id = value.strip()
    mapping = {
        "chat": "chat_id",
        "group": "chat_id",
        "chat_id": "chat_id",
        "user": "user_id",
        "user_id": "user_id",
        "open_id": "open_id",
        "union_id": "union_id",
        "email": "email",
    }
    receive_id_type = mapping.get(prefix.strip().lower())
    if not receive_id_type or not receive_id:
        raise NotifyError(f"Unsupported Feishu target: {raw_target}")
    return receive_id, receive_id_type


def parse_config_file(config_path: str) -> dict[str, Any]:
    raw = read_text_file(config_path)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NotifyError(f"Failed to parse config file {config_path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise NotifyError(f"Config file {config_path} must contain a JSON object")
    return parsed


def load_config_sources(args: argparse.Namespace) -> ConfigSources:
    config_path = normalize_string(args.config) or normalize_string(os.environ.get("FEISHU_NOTIFY_CONFIG"))
    if not config_path:
        return ConfigSources(config_path=None, config_data={})
    return ConfigSources(config_path=config_path, config_data=parse_config_file(config_path))


def get_config_value(config_data: dict[str, Any], *keys: str) -> Optional[str]:
    candidates: list[Any] = [config_data]
    feishu_section = config_data.get("feishu")
    if isinstance(feishu_section, dict):
        candidates.insert(0, feishu_section)
    notifier_section = config_data.get("notifier")
    if isinstance(notifier_section, dict):
        candidates.insert(0, notifier_section)
    for container in candidates:
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = normalize_string(container.get(key))
            if value:
                return value
    return None


def resolve_missing_config_error(sources: ConfigSources, *, missing_app: bool, missing_secret: bool, missing_target: bool) -> NotifyError:
    missing_fields: list[str] = []
    if missing_app:
        missing_fields.append("FEISHU_APP_ID")
    if missing_secret:
        missing_fields.append("FEISHU_APP_SECRET")
    if missing_target:
        missing_fields.append("FEISHU_NOTIFY_TO")
    joined = ", ".join(missing_fields)
    if sources.config_path:
        return NotifyError(
            f"Missing {joined}. The script checked environment variables and config file {sources.config_path}. Please ask the user to provide the missing Feishu settings or update the config file."
        )
    return NotifyError(
        f"Missing {joined}. No usable Feishu settings were found in environment variables or a config file. Please ask the user for the Feishu app id, app secret, and notification target, or provide --config/FEISHU_NOTIFY_CONFIG."
    )


def read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def load_payload(payload_path: Optional[str]) -> Any:
    raw: Optional[str] = None
    if payload_path:
        raw = sys.stdin.read() if payload_path == "-" else read_text_file(payload_path)
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def extract_error(payload: dict[str, Any]) -> Optional[str]:
    error = payload.get("error")
    if isinstance(error, str):
        return error.strip() or None
    if isinstance(error, dict):
        return first_non_empty(error.get("message"), error.get("msg"), compact_json(error))
    return None


def extract_status(explicit_status: Optional[str], payload: Optional[dict[str, Any]]) -> Optional[str]:
    if explicit_status:
        return explicit_status.strip()
    if not payload:
        return None
    status = first_non_empty(payload.get("status"), payload.get("result"), payload.get("state"))
    if status:
        return status
    success = payload.get("success")
    if isinstance(success, bool):
        return "success" if success else "failed"
    return None


def format_duration(duration_ms: Any) -> Optional[str]:
    if isinstance(duration_ms, bool):
        return None
    if not isinstance(duration_ms, (int, float)):
        return None
    seconds = float(duration_ms) / 1000.0
    if seconds < 1:
        return f"{int(duration_ms)} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, remainder = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {remainder}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {remainder}s"


def build_message(args: argparse.Namespace, payload: Any) -> str:
    payload_dict = payload if isinstance(payload, dict) else None
    title = first_non_empty(
        args.title,
        payload_dict.get("title") if payload_dict else None,
        "Agent notification",
    )
    if args.message:
        return args.message.strip()
    if isinstance(payload, str):
        return payload.strip()

    event = first_non_empty(args.event, payload_dict.get("event") if payload_dict else None, payload_dict.get("hook") if payload_dict else None)
    status = extract_status(args.status, payload_dict)
    agent = first_non_empty(
        args.agent,
        payload_dict.get("agent") if payload_dict else None,
        payload_dict.get("agentId") if payload_dict else None,
        payload_dict.get("agent_id") if payload_dict else None,
    )
    task = first_non_empty(
        args.task,
        payload_dict.get("task") if payload_dict else None,
        payload_dict.get("taskName") if payload_dict else None,
        payload_dict.get("objective") if payload_dict else None,
        payload_dict.get("description") if payload_dict else None,
        payload_dict.get("prompt") if payload_dict else None,
    )
    session = first_non_empty(
        args.session,
        payload_dict.get("session") if payload_dict else None,
        payload_dict.get("sessionKey") if payload_dict else None,
        payload_dict.get("session_id") if payload_dict else None,
    )
    summary = truncate(
        first_non_empty(
            args.summary,
            payload_dict.get("summary") if payload_dict else None,
            payload_dict.get("result") if payload_dict else None,
            payload_dict.get("message") if payload_dict else None,
            payload_dict.get("text") if payload_dict else None,
        )
    )
    error = truncate(first_non_empty(args.error, extract_error(payload_dict) if payload_dict else None))
    duration_source = args.duration_ms
    if duration_source is None and payload_dict:
        duration_source = payload_dict.get("durationMs")
    duration = format_duration(duration_source)
    now_text = datetime.now().astimezone().replace(microsecond=0).isoformat()

    lines = [f"🔔 {title}"]
    if event:
        lines.append(f"事件: {event}")
    if status:
        lines.append(f"状态: {status}")
    if agent:
        lines.append(f"Agent: {agent}")
    if task:
        lines.append(f"任务: {truncate(task, 200)}")
    if session:
        lines.append(f"Session: {session}")
    if duration:
        lines.append(f"耗时: {duration}")
    if summary:
        lines.append(f"摘要: {summary}")
    if error:
        lines.append(f"错误: {error}")
    lines.append(f"时间: {now_text}")
    return "\n".join(lines)


def resolve_config(args: argparse.Namespace) -> FeishuConfig:
    sources = load_config_sources(args)
    app_id = first_non_empty(
        args.app_id,
        os.environ.get("FEISHU_APP_ID"),
        get_config_value(sources.config_data, "appId", "app_id", "feishuAppId"),
    ) or ""
    app_secret = first_non_empty(
        args.app_secret,
        os.environ.get("FEISHU_APP_SECRET"),
        get_config_value(sources.config_data, "appSecret", "app_secret", "feishuAppSecret"),
    ) or ""
    target = first_non_empty(
        args.to,
        os.environ.get("FEISHU_NOTIFY_TO"),
        get_config_value(sources.config_data, "notifyTo", "target", "to", "receiveId", "receive_id"),
    ) or ""
    domain = first_non_empty(
        args.domain,
        os.environ.get("FEISHU_DOMAIN"),
        get_config_value(sources.config_data, "domain", "feishuDomain"),
        "feishu",
    ) or "feishu"

    missing_app = not app_id
    missing_secret = not app_secret
    missing_target = not target
    if missing_target:
        raise resolve_missing_config_error(
            sources,
            missing_app=missing_app and not args.dry_run,
            missing_secret=missing_secret and not args.dry_run,
            missing_target=True,
        )
    if args.dry_run:
        return FeishuConfig(app_id=app_id or "dry-run-app", app_secret=app_secret or "dry-run-secret", target=target, domain=domain)
    if missing_app or missing_secret:
        raise resolve_missing_config_error(
            sources,
            missing_app=missing_app,
            missing_secret=missing_secret,
            missing_target=False,
        )
    return FeishuConfig(app_id=app_id, app_secret=app_secret, target=target, domain=domain)


def request_json(url: str, *, method: str = "GET", headers: Optional[dict[str, str]] = None, data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    body = None
    merged_headers = {"Accept": "application/json"}
    if headers:
        merged_headers.update(headers)
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        merged_headers.setdefault("Content-Type", "application/json; charset=utf-8")
    request = urllib.request.Request(url, data=body, headers=merged_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise NotifyError(f"HTTP {exc.code} while calling Feishu API: {detail}") from exc
    except urllib.error.URLError as exc:
        raise NotifyError(f"Network error while calling Feishu API: {exc.reason}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NotifyError(f"Failed to parse Feishu API response: {exc}") from exc
    if not isinstance(payload, dict):
        raise NotifyError("Unexpected Feishu API response format")
    return payload


def get_tenant_access_token(config: FeishuConfig) -> str:
    base_url = resolve_api_base(config.domain)
    payload = request_json(
        f"{base_url}/auth/v3/tenant_access_token/internal",
        method="POST",
        data={"app_id": config.app_id, "app_secret": config.app_secret},
    )
    if payload.get("code") != 0 or not isinstance(payload.get("tenant_access_token"), str):
        raise NotifyError(f"Failed to get tenant access token: {payload.get('msg') or compact_json(payload)}")
    return payload["tenant_access_token"]


def send_text_message(config: FeishuConfig, message: str) -> dict[str, Any]:
    receive_id, receive_id_type = parse_target(config.target)
    base_url = resolve_api_base(config.domain)
    token = get_tenant_access_token(config)
    query = urllib.parse.urlencode({"receive_id_type": receive_id_type})
    payload = request_json(
        f"{base_url}/im/v1/messages?{query}",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": message}, ensure_ascii=False),
        },
    )
    if payload.get("code") != 0:
        raise NotifyError(f"Feishu message send failed: {payload.get('msg') or compact_json(payload)}")
    return payload


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send compact Feishu notifications for agent events.")
    parser.add_argument("--app-id", help="Feishu app id. Defaults to FEISHU_APP_ID.")
    parser.add_argument("--app-secret", help="Feishu app secret. Defaults to FEISHU_APP_SECRET.")
    parser.add_argument("--domain", help="feishu, lark, or custom base URL. Defaults to FEISHU_DOMAIN or feishu.")
    parser.add_argument("--config", help="Path to a JSON config file. Defaults to FEISHU_NOTIFY_CONFIG when set.")
    parser.add_argument("--to", help="Target such as chat:oc_xxx, user:ou_xxx, open_id:ou_xxx, or email:name@example.com.")
    parser.add_argument("--message", help="Send this exact message without auto-formatting.")
    parser.add_argument("--title", help="Notification title for generated messages.")
    parser.add_argument("--event", help="Event name such as agent_end.")
    parser.add_argument("--status", help="Status such as success, failed, waiting, or need_input.")
    parser.add_argument("--agent", help="Agent identifier.")
    parser.add_argument("--task", help="Task or objective summary.")
    parser.add_argument("--session", help="Session identifier.")
    parser.add_argument("--summary", help="Short completion summary.")
    parser.add_argument("--error", help="Short error summary.")
    parser.add_argument("--duration-ms", type=float, help="Duration in milliseconds.")
    parser.add_argument("--payload", help="Structured payload path, or '-' for stdin. If omitted and stdin is piped, stdin is used automatically.")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format for local result reporting.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--dry-run", action="store_true", help="Render the message and resolved routing without sending it.")
    return parser


def emit_result(args: argparse.Namespace, result: dict[str, Any]) -> None:
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
        return
    for key in ["ok", "dryRun", "target", "receiveIdType", "messageId", "message"]:
        if key in result and result[key] is not None:
            print(f"{key}: {result[key]}")


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    try:
        payload = load_payload(args.payload)
        message = build_message(args, payload)
        if not message:
            raise NotifyError("Empty notification message")
        config = resolve_config(args)
        _, receive_id_type = parse_target(config.target)

        if args.dry_run:
            emit_result(
                args,
                {
                    "ok": True,
                    "dryRun": True,
                    "target": config.target,
                    "receiveIdType": receive_id_type,
                    "message": message,
                },
            )
            return 0

        response = send_text_message(config, message)
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        emit_result(
            args,
            {
                "ok": True,
                "dryRun": False,
                "target": config.target,
                "receiveIdType": receive_id_type,
                "messageId": data.get("message_id") if isinstance(data, dict) else None,
                "message": message,
            },
        )
        return 0
    except NotifyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
