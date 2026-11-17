"""Command router: start, help, cancel, stats, settings and the shared menu callbacks."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import library_views, navigation, views
from src.bot.keyboards import HELP_TEXTS, MenuCB
from src.core.i18n import parse_language, pick, set_language
from src.services.container import Services

router = Router(name="commands")


async def _main_menu(
    message: Message, state: FSMContext, bot, services: Services
) -> None:
    """Wipe the whole log and leave a single fresh welcome screen behind."""
    await views.clear_tracked(bot, message.chat.id, state, upto=message.message_id)
    await views.reset_flow(state, keep_nav=False)
    await state.update_data(trail=[], screen_key=dict(navigation.MAIN))
    await navigation.navigate_message(
        message, state, bot, services, navigation.MAIN, push=False
    )
    await views.remember(state, message.message_id)


@router.message(CommandStart())
async def cmd_start(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    await _main_menu(message, state, bot, services)


@router.message(Command("help"))
@router.message(F.text.in_(HELP_TEXTS))
async def cmd_help(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await views.remember(state, message.message_id)
    await navigation.navigate_message(message, state, bot, services, {"k": "help"})


@router.message(Command("cancel"))
async def cmd_cancel(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    await _main_menu(message, state, bot, services)


@router.callback_query(MenuCB.filter(F.action == "main"))
async def menu_main_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    if callback.message is not None:
        await views.clear_tracked(
            bot,
            callback.message.chat.id,
            state,
            upto=callback.message.message_id,
        )
    await views.reset_flow(state, keep_nav=False)
    await state.update_data(trail=[], screen_key=dict(navigation.MAIN))
    await navigation.navigate_callback(
        callback, state, bot, services, navigation.MAIN, push=False
    )


@router.callback_query(MenuCB.filter(F.action == "back"))
async def menu_back_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    # Going back changes the screen; the copies opened from it are removed by
    # go_back itself, which is why the flow is reset only afterwards.
    await navigation.go_back(callback, state, services)
    await views.reset_flow(state)


@router.callback_query(MenuCB.filter(F.action == "settings"))
async def menu_settings_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Open the settings screen (language, groups, help)."""
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "settings", "u": int(callback.from_user.id)},
    )


@router.callback_query(MenuCB.filter(F.action == "toggle_saved"))
async def menu_toggle_saved_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Turn the «✅ The file was saved» card on or off for this user."""
    user_id = int(callback.from_user.id)
    show = services.preferences.show_saved_screen(user_id)
    show = await services.preferences.set_saved_screen(user_id, not show)
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "settings", "u": user_id},
        notice=library_views.saved_screen_changed_text(show),
        push=False,
    )


@router.callback_query(MenuCB.filter(F.action == "toggle_keep_sent"))
async def menu_toggle_keep_sent_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Keep (or not) the files opened from a file screen after «↩️ Back»."""
    user_id = int(callback.from_user.id)
    keep = services.preferences.keep_sent_files(user_id)
    keep = await services.preferences.set_keep_sent_files(user_id, not keep)
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "settings", "u": user_id},
        notice=library_views.keep_sent_changed_text(keep),
        push=False,
    )


@router.callback_query(MenuCB.filter(F.action == "language"))
async def menu_language_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Open the language picker."""
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback, state, bot, services, {"k": "language"}
    )


@router.callback_query(MenuCB.filter(F.action == "set_lang"))
async def menu_set_language_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Store the choice, switch the language and redraw the picker in it."""
    data = MenuCB.unpack(callback.data)
    language = parse_language(data.value)
    if callback.from_user is not None:
        await services.preferences.set_language(int(callback.from_user.id), language)
    set_language(language)
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "language"},
        notice=views.language_changed_text(language),
        push=False,
    )


@router.callback_query(MenuCB.filter(F.action == "help"))
async def menu_help_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback, state, bot, services, {"k": "help"}
    )


@router.callback_query(MenuCB.filter(F.action == "cancel"))
async def menu_cancel_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        navigation.MAIN,
        notice=pick("✅ The operation was cancelled.", "✅ تم إلغاء العملية."),
        push=False,
    )


@router.callback_query(MenuCB.filter(F.action == "stats"))
async def menu_stats_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Statistics are opened from the status screen, so back returns there."""
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback, state, bot, services, {"k": "stats"}
    )


@router.message(Command("stats"))
async def cmd_stats(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    await views.reset_flow(state)
    await views.remember(state, message.message_id)
    await navigation.navigate_message(message, state, bot, services, {"k": "stats"})
