import os
from typing import List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application Settings loaded from environment variables and .env file.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Telegram Bot
    BOT_TOKEN: str = Field(..., description="Telegram Bot Token from @BotFather")

    # Telegram Storage Chat/Group IDs (Physical Storage Backend)
    STORAGE_DOCUMENTS_CHAT_ID: int = Field(..., description="Group ID for documents (PDF, DOCX, etc.)")
    STORAGE_MEDIA_CHAT_ID: int = Field(..., description="Group ID for media (Images, Videos, GIFs)")
    STORAGE_AUDIO_CHAT_ID: int = Field(..., description="Group ID for audio (Music, Voice)")
    STORAGE_ARCHIVES_CHAT_ID: int = Field(..., description="Group ID for compressed files (ZIP, RAR, 7Z, TAR)")
    STORAGE_DEFAULT_CHAT_ID: int = Field(..., description="Fallback Group ID for uncategorized files")

    # Supabase / Database
    SUPABASE_URL: str = Field(..., description="Supabase project URL")
    SUPABASE_SERVICE_ROLE_KEY: str = Field(..., description="Supabase Service Role Key (full access)")
    DATABASE_URL: str = Field(default="", description="Direct PostgreSQL connection string if needed")

    # Admins
    ADMIN_IDS: List[int] = Field(default_factory=list, description="List of Admin Telegram User IDs")

    # UI & Pagination
    DEFAULT_PAGE_SIZE: int = Field(default=5, description="Files per page in pagination")
    MAX_SEARCH_RESULTS: int = Field(default=10, description="Max search results to display")

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, v):
        if isinstance(v, str):
            if not v.strip():
                return []
            return [int(x.strip()) for x in v.split(",") if x.strip().isdigit()]
        return v or []


settings = Settings()
