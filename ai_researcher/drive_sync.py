from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import db


DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
DEFAULT_ROOT_FOLDER = "AI Researcher"


def authorize_drive(config: dict[str, Any], *, port: int = 8080, bind_host: str = "0.0.0.0") -> dict[str, Any]:
    drive_config = _drive_config(config)
    creds = _load_credentials(drive_config)
    if creds and creds.valid:
        return {"status": "ok", "reason": "existing token is valid", "token_path": _token_path(drive_config)}

    creds = _run_oauth_flow(drive_config, port=port, bind_host=bind_host)
    _save_credentials(creds, _token_path(drive_config))
    return {
        "status": "ok",
        "reason": "authorized",
        "token_path": _token_path(drive_config),
        "scopes": _scopes(drive_config),
    }


def sync_report(
    conn,
    config: dict[str, Any],
    *,
    report_path: str | Path,
    period: str,
    label: str,
) -> dict[str, Any]:
    drive_config = _drive_config(config)
    if not drive_config.get("enabled", False):
        return {"status": "skipped", "reason": "disabled"}

    try:
        return _sync_report(conn, drive_config, report_path=Path(report_path), period=period, label=label)
    except Exception as exc:
        result = {"status": "failed", "reason": str(exc)}
        try:
            db.upsert_drive_file(
                conn,
                local_path=str(report_path),
                period=period,
                label=label,
                drive_file_id=None,
                web_view_link=None,
                mime_type=None,
                status="failed",
                metadata={"error": str(exc)},
            )
            conn.commit()
        except Exception:
            pass
        if drive_config.get("fail_report_on_error", False):
            raise
        return result


def sync_existing_reports(conn, config: dict[str, Any], *, root: str | Path = "reports") -> dict[str, Any]:
    drive_config = _drive_config(config)
    if not drive_config.get("enabled", False):
        return {"status": "skipped", "reason": "disabled"}

    root_path = Path(root)
    synced = []
    failed = []
    for period in ("daily", "weekly", "monthly", "evolution"):
        period_dir = root_path / period
        if not period_dir.exists():
            continue
        for report_path in sorted(period_dir.glob("*.md")):
            label = report_path.stem
            result = sync_report(conn, config, report_path=report_path, period=period, label=label)
            if result.get("status") == "synced":
                synced.append(result)
            else:
                failed.append({"path": str(report_path), **result})

    return {"status": "ok" if not failed else "partial", "synced": len(synced), "failed": failed}


def sync_one_report(
    conn,
    config: dict[str, Any],
    *,
    report_path: str | Path,
    period: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    path = Path(report_path)
    if period and label:
        resolved_period, resolved_label = period, label
    else:
        inferred_period, inferred_label = _infer_period_label(path)
        resolved_period = period or inferred_period
        resolved_label = label or inferred_label
    return sync_report(
        conn,
        config,
        report_path=path,
        period=resolved_period,
        label=resolved_label,
    )


def _sync_report(conn, drive_config: dict[str, Any], *, report_path: Path, period: str, label: str) -> dict[str, Any]:
    if not report_path.exists():
        raise FileNotFoundError(f"Report file not found: {report_path}")

    service = _drive_service(drive_config)
    root_id = _ensure_folder(service, _root_folder_name(drive_config), parent_id="root")
    folder_id = _ensure_folder(service, period, parent_id=root_id)
    mime_type = drive_config.get("mime_type") or _guess_mime_type(report_path)
    file_name = report_path.name

    existing = _find_file(service, file_name, parent_id=folder_id)
    MediaFileUpload = _media_file_upload_class()
    media = MediaFileUpload(str(report_path), mimetype=mime_type, resumable=False)
    if existing:
        file = (
            service.files()
            .update(
                fileId=existing["id"],
                media_body=media,
                fields="id,name,webViewLink,modifiedTime,mimeType",
            )
            .execute()
        )
        action = "updated"
    else:
        file_metadata = {"name": file_name, "parents": [folder_id]}
        file = (
            service.files()
            .create(
                body=file_metadata,
                media_body=media,
                fields="id,name,webViewLink,modifiedTime,mimeType",
            )
            .execute()
        )
        action = "created"

    result = {
        "status": "synced",
        "action": action,
        "file_id": file["id"],
        "web_view_link": file.get("webViewLink"),
        "mime_type": file.get("mimeType") or mime_type,
        "modified_time": file.get("modifiedTime"),
    }
    db.upsert_drive_file(
        conn,
        local_path=str(report_path),
        period=period,
        label=label,
        drive_file_id=file["id"],
        web_view_link=file.get("webViewLink"),
        mime_type=file.get("mimeType") or mime_type,
        status="synced",
        metadata={"action": action, "modified_time": file.get("modifiedTime")},
    )
    conn.commit()
    return result


def _drive_service(drive_config: dict[str, Any]):
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("google-api-python-client is required for Google Drive sync") from exc

    creds = _ensure_credentials(drive_config)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _ensure_credentials(drive_config: dict[str, Any]) -> Any:
    creds = _load_credentials(drive_config)
    if not creds:
        raise RuntimeError("Google Drive OAuth token is missing; run drive-auth first")
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            from google.auth.transport.requests import Request
        except ImportError as exc:
            raise RuntimeError("google-auth is required for Google Drive sync") from exc

        creds.refresh(Request())
        _save_credentials(creds, _token_path(drive_config))
        return creds
    raise RuntimeError("Google Drive OAuth token is invalid; run drive-auth again")


def _load_credentials(drive_config: dict[str, Any]) -> Any | None:
    token_path = Path(_token_path(drive_config))
    if not token_path.exists():
        return None
    try:
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise RuntimeError("google-auth is required for Google Drive sync") from exc

    return Credentials.from_authorized_user_file(str(token_path), _scopes(drive_config))


def _run_oauth_flow(drive_config: dict[str, Any], *, port: int, bind_host: str) -> Any:
    client_secret_path = _client_secret_path(drive_config)
    if not Path(client_secret_path).exists():
        raise FileNotFoundError(f"Google Drive OAuth client secret not found: {client_secret_path}")

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError("google-auth-oauthlib is required for Google Drive authorization") from exc

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, _scopes(drive_config))
    return flow.run_local_server(
        host="localhost",
        bind_addr=bind_host,
        port=port,
        open_browser=False,
        authorization_prompt_message=(
            "Open this URL in your browser to authorize Google Drive access:\n{url}\n"
        ),
        success_message="Google Drive authorization completed. You can close this browser tab.",
    )


def _save_credentials(creds: Any, token_path: str) -> None:
    path = Path(token_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")


def _media_file_upload_class():
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise RuntimeError("google-api-python-client is required for Google Drive sync") from exc

    return MediaFileUpload


def _ensure_folder(service, name: str, *, parent_id: str) -> str:
    folder = _find_folder(service, name, parent_id=parent_id)
    if folder:
        return folder["id"]

    metadata = {
        "name": name,
        "mimeType": FOLDER_MIME_TYPE,
        "parents": [parent_id],
    }
    created = service.files().create(body=metadata, fields="id").execute()
    return created["id"]


def _find_folder(service, name: str, *, parent_id: str) -> dict[str, Any] | None:
    return _find_file(service, name, parent_id=parent_id, mime_type=FOLDER_MIME_TYPE)


def _find_file(
    service,
    name: str,
    *,
    parent_id: str,
    mime_type: str | None = None,
) -> dict[str, Any] | None:
    q = [
        f"name = '{_escape_query_value(name)}'",
        f"'{_escape_query_value(parent_id)}' in parents",
        "trashed = false",
    ]
    if mime_type:
        q.append(f"mimeType = '{_escape_query_value(mime_type)}'")
    result = (
        service.files()
        .list(
            q=" and ".join(q),
            spaces="drive",
            pageSize=1,
            fields="files(id,name,webViewLink,mimeType)",
        )
        .execute()
    )
    files = result.get("files") or []
    return files[0] if files else None


def _escape_query_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def _drive_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("integrations", {}).get("google_drive", {})


def _scopes(drive_config: dict[str, Any]) -> list[str]:
    return list(drive_config.get("scopes") or [DRIVE_FILE_SCOPE])


def _root_folder_name(drive_config: dict[str, Any]) -> str:
    return str(drive_config.get("root_folder_name") or DEFAULT_ROOT_FOLDER)


def _token_path(drive_config: dict[str, Any]) -> str:
    env_name = str(drive_config.get("token_env") or "GOOGLE_DRIVE_TOKEN")
    return os.environ.get(env_name) or str(drive_config.get("token_path") or "secrets/google-drive-token.json")


def _client_secret_path(drive_config: dict[str, Any]) -> str:
    env_name = str(drive_config.get("client_secret_env") or "GOOGLE_DRIVE_CLIENT_SECRET")
    return os.environ.get(env_name) or str(
        drive_config.get("client_secret_path") or "secrets/google-drive-oauth-client.json"
    )


def _guess_mime_type(path: Path) -> str:
    if path.suffix.lower() == ".md":
        return "text/markdown"
    if path.suffix.lower() == ".txt":
        return "text/plain"
    return "application/octet-stream"


def _infer_period_label(path: Path) -> tuple[str, str]:
    parts = path.parts
    if len(parts) >= 2 and parts[-2] in {"daily", "weekly", "monthly", "evolution"}:
        return parts[-2], path.stem
    raise ValueError("period and label are required when the path is not under reports/<period>/")
