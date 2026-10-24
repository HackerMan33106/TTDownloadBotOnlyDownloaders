"""Проверка доступа до любых обработчиков и фильтров роутеров."""
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Dispatcher, types
from aiogram.exceptions import TelegramAPIError

from database.admins import is_admin
from database.limits import is_blacklisted, is_globally_banned
from database.whitelist import is_group_whitelisted, is_user_whitelisted


logger = logging.getLogger(__name__)


class AccessMiddleware(BaseMiddleware):
    async def is_allowed(self, user: types.User | None, chat: types.Chat | None) -> bool:
        if user is None:
            return False

        # Администраторы должны иметь возможность управлять доступом в любом чате.
        if await is_admin(user.id):
            return True
        if await is_globally_banned(user.id) or await is_blacklisted(user.id):
            return False

        if chat is not None and chat.type in ("group", "supergroup"):
            return await is_group_whitelisted(chat.id)
        if chat is None or chat.type == "private":
            # Inline-запросы и кнопки без чата требуют личного допуска.
            return await is_user_whitelisted(user.id)
        return False

    async def __call__(
        self,
        handler: Callable[[types.Update, dict[str, Any]], Awaitable[Any]],
        event: types.Update,
        data: dict[str, Any],
    ) -> Any:
        # UserContextMiddleware диспетчера заполняет их для сообщений,
        # редактирований, callback_query и inline-запросов.
        user = data.get("event_from_user")
        chat = data.get("event_chat")
        try:
            allowed = await self.is_allowed(user, chat)
        except Exception:
            # При сбое БД нельзя пропускать запрос без проверки доступа.
            logger.exception("Не удалось проверить доступ к боту")
            allowed = False

        if allowed:
            return await handler(event, data)

        logger.info(
            "🚫 Доступ к боту запрещён | user_id=%s | chat_id=%s",
            user.id if user else None,
            chat.id if chat else None,
        )
        if event.callback_query is not None:
            try:
                await data["bot"].answer_callback_query(
                    event.callback_query.id,
                    text="❌ У вас нет доступа к этому боту",
                    show_alert=True,
                )
            except TelegramAPIError:
                pass  # Старый callback уже мог истечь.
        # Посторонним не отправляем сообщения и не создаём новые кнопки.
        return None


def register_access_middleware(dp: Dispatcher) -> None:
    if not any(isinstance(item, AccessMiddleware) for item in dp.update.outer_middleware):
        dp.update.outer_middleware(AccessMiddleware())
