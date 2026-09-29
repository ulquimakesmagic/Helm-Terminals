"""aiogram adapter. Run with: python -m helm_bot   (needs TELEGRAM_BOT_TOKEN, HELM_API_URL, BOT_API_KEY)."""

import asyncio
import logging
from contextlib import suppress

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Message
from pydantic_settings import BaseSettings, SettingsConfigDict

from .api import BotApi
from .controller import Controller, Reply


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    telegram_bot_token: str
    helm_api_url: str = "http://localhost:8000"
    bot_api_key: str


COMMANDS = [
    ("wallets", "Your wallets and balances"), ("launches", "Your launches"), ("launch", "Launch details: /launch <id>"),
    ("transparency", "Supply held by your wallets"), ("status", "Job status"),
    ("unlink", "Disconnect this Telegram"), ("help", "Help"),
]


def keyboard(r: Reply) -> InlineKeyboardMarkup | None:
    if not r.buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=b.label, callback_data=b.data) for b in row]
                                                 for row in r.buttons])


async def run() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = BotSettings()
    http = httpx.AsyncClient(base_url=cfg.helm_api_url, timeout=30)
    ctl = Controller(BotApi(http, cfg.bot_api_key))
    bot = Bot(cfg.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    no_preview = LinkPreviewOptions(is_disabled=True)

    @dp.message(F.text)
    async def on_text(m: Message) -> None:
        async def send(r: Reply) -> None:
            if r.delete_incoming:
                with suppress(Exception):
                    await m.delete()  # 2FA codes and link codes should not linger in the chat
            await m.answer(r.text, reply_markup=keyboard(r), link_preview_options=no_preview)

        assert m.from_user is not None and m.text is not None
        await ctl.on_message(m.from_user.id, m.chat.id, m.chat.type, m.text, send)

    @dp.callback_query()
    async def on_button(c: CallbackQuery) -> None:
        await c.answer()
        if not isinstance(c.message, Message) or c.data is None:
            return
        with suppress(Exception):
            await c.message.edit_reply_markup(reply_markup=None)  # one tap only

        async def send(r: Reply) -> None:
            await c.message.answer(r.text, reply_markup=keyboard(r), link_preview_options=no_preview)  # type: ignore[union-attr]

        await ctl.on_callback(c.from_user.id, c.message.chat.type, c.data, send)

    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS])
    try:
        await dp.start_polling(bot)
    finally:
        await http.aclose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())
