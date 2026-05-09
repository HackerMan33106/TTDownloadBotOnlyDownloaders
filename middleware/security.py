from aiogram import BaseMiddleware, types
from typing import Callable, Any, Awaitable
from utils.crypto import verify_callback, logger

class CallbackSecurityMiddleware(BaseMiddleware):
    async def __call__(
        self, 
        handler: Callable[[types.TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: types.CallbackQuery, 
        data: dict[str, Any]
    ) -> Any:
        
        if event.data:
            original_data = verify_callback(event.data)
            if original_data is None:
                # Если подпись неверная или старая критичная кнопка без подписи
                logger.warning(f"🚫 Заблокирован callback_query без подписи от {event.from_user.id}: {event.data}")
                try:
                    await event.answer("❌ Кнопка устарела или неверная подпись.", show_alert=True)
                except:
                    pass
                return
            
            # Подменяем data на оригинальные для дальнейших фильтров и хэндлеров
            event = event.model_copy(update={'data': original_data})
            
        return await handler(event, data)
