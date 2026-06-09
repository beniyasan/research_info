from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from . import db
from .preferences import record_feedback


DISCORD_BOT_TOKEN_ENV = "DISCORD_BOT_TOKEN"
DISCORD_CHANNEL_ID_ENV = "DISCORD_CHANNEL_ID"
DISCORD_ALLOWED_USER_IDS_ENV = "DISCORD_ALLOWED_USER_IDS"
CUSTOM_ID_PREFIX = "air"
CUSTOM_ID_MAX_LENGTH = 100

try:
    import discord
except ImportError:  # pragma: no cover - exercised only when the optional bot dependency is absent.
    discord = None  # type: ignore[assignment]


@dataclass(frozen=True)
class FeedbackComponentId:
    raw: str
    action: str
    report_key: str
    article_id: str


def make_rate_custom_id(report_key: str, article_id: str) -> str:
    return _make_custom_id("rate", report_key, article_id)


def make_comment_custom_id(report_key: str, article_id: str) -> str:
    return _make_custom_id("comment", report_key, article_id)


def parse_component_custom_id(value: str | None) -> FeedbackComponentId | None:
    if not value:
        return None
    parts = value.split(":", 3)
    if len(parts) != 4 or parts[0] != CUSTOM_ID_PREFIX or parts[1] not in {"rate", "comment"}:
        return None
    return FeedbackComponentId(
        raw=value,
        action=parts[1],
        report_key=unquote(parts[2]),
        article_id=unquote(parts[3]),
    )


def parse_allowed_user_ids(value: str | None = None) -> set[str]:
    raw = os.environ.get(DISCORD_ALLOWED_USER_IDS_ENV, "") if value is None else value
    return {part.strip() for part in raw.split(",") if part.strip()}


def run_discord_bot(db_path: str | Path) -> None:
    if discord is None:
        raise RuntimeError("discord.py is required for the discord-bot command")
    token = os.environ.get(DISCORD_BOT_TOKEN_ENV)
    if not token:
        raise RuntimeError(f"{DISCORD_BOT_TOKEN_ENV} is required")

    intents = discord.Intents.default()
    client = FeedbackClient(db_path, intents=intents)
    client.run(token)


class FeedbackClient(discord.Client if discord is not None else object):  # type: ignore[misc]
    def __init__(self, db_path: str | Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.db_path = str(db_path)
        self.allowed_user_ids = parse_allowed_user_ids()

    async def on_ready(self) -> None:
        print(f"AI Researcher feedback bot ready: {self.user}")

    async def on_interaction(self, interaction: Any) -> None:
        if discord is None:
            return
        if interaction.type == discord.InteractionType.component:
            await self._handle_component(interaction)
        elif interaction.type == discord.InteractionType.modal_submit:
            await self._handle_modal_submit(interaction)

    async def _handle_component(self, interaction: Any) -> None:
        component_id = parse_component_custom_id((interaction.data or {}).get("custom_id"))
        if component_id is None:
            return
        if not self._user_allowed(interaction):
            await interaction.response.send_message("この評価操作は許可されていません。", ephemeral=True)
            return

        if component_id.action == "rate":
            values = (interaction.data or {}).get("values") or []
            rating = str(values[0]) if values else ""
            try:
                self._record_feedback(
                    component_id,
                    interaction,
                    rating=rating,
                )
            except Exception as exc:
                await interaction.response.send_message(f"評価の保存に失敗しました: {exc}", ephemeral=True)
                return
            await interaction.response.send_message(f"評価を保存しました: {rating}", ephemeral=True)
            return

        if component_id.action == "comment":
            await interaction.response.send_modal(FeedbackCommentModal(self, component_id))

    async def _handle_modal_submit(self, interaction: Any) -> None:
        component_id = parse_component_custom_id((interaction.data or {}).get("custom_id"))
        if component_id is None or component_id.action != "comment":
            return
        if not self._user_allowed(interaction):
            await interaction.response.send_message("この評価操作は許可されていません。", ephemeral=True)
            return
        comment = _extract_modal_text(interaction.data or {})
        if not comment:
            await interaction.response.send_message("コメントが空です。", ephemeral=True)
            return
        try:
            self._record_feedback(
                component_id,
                interaction,
                comment=comment,
            )
        except Exception as exc:
            await interaction.response.send_message(f"コメントの保存に失敗しました: {exc}", ephemeral=True)
            return
        await interaction.response.send_message("コメントを保存しました。", ephemeral=True)

    def _record_feedback(
        self,
        component_id: FeedbackComponentId,
        interaction: Any,
        *,
        rating: str | None = None,
        comment: str | None = None,
    ) -> None:
        message = getattr(interaction, "message", None)
        channel = getattr(interaction, "channel", None)
        conn = db.connect(self.db_path)
        try:
            db.init_db(conn)
            record_feedback(
                conn,
                report_key=component_id.report_key,
                article_id=component_id.article_id,
                discord_user_id=interaction.user.id,
                rating=rating,
                comment=comment,
                message_id=getattr(message, "id", None),
                channel_id=getattr(channel, "id", None),
                metadata={"interaction_id": str(interaction.id)},
            )
            conn.commit()
        finally:
            conn.close()

    def _user_allowed(self, interaction: Any) -> bool:
        if not self.allowed_user_ids:
            return True
        return str(interaction.user.id) in self.allowed_user_ids


if discord is not None:

    class FeedbackCommentModal(discord.ui.Modal):
        comment = discord.ui.TextInput(
            label="コメント",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
        )

        def __init__(self, client: FeedbackClient, component_id: FeedbackComponentId) -> None:
            super().__init__(title="記事へのコメント", custom_id=component_id.raw)
            self.feedback_client = client
            self.component_id = component_id

else:
    FeedbackCommentModal = None  # type: ignore[assignment]


def _make_custom_id(action: str, report_key: str, article_id: str) -> str:
    value = f"{CUSTOM_ID_PREFIX}:{action}:{quote(report_key, safe='')}:{quote(article_id, safe='')}"
    if len(value) > CUSTOM_ID_MAX_LENGTH:
        raise ValueError(f"Discord custom_id is too long: {len(value)}")
    return value


def _extract_modal_text(data: dict[str, Any]) -> str:
    values: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if "value" in node and node["value"]:
                values.append(str(node["value"]))
            for child in node.get("components") or []:
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(data.get("components") or [])
    return "\n".join(values).strip()
