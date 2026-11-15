"""FSM states."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class SearchStates(StatesGroup):
    waiting_query = State()


class RenameStates(StatesGroup):
    waiting_name = State()


class FolderStates(StatesGroup):
    waiting_name = State()


class JoinStates(StatesGroup):
    waiting_password = State()
