from enum import Enum
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


class FileCategory(str, Enum):
    DOCUMENTS = "documents"
    MEDIA = "media"
    AUDIO = "audio"
    ARCHIVES = "archives"
    OTHER = "other"


class FileMetadataCreate(BaseModel):
    user_id: int
    file_id: str
    file_unique_id: str
    file_name: str
    file_size: int
    mime_type: Optional[str] = None
    category: FileCategory = FileCategory.OTHER
    storage_chat_id: int
    storage_message_id: int


class FileMetadata(FileMetadataCreate):
    id: str
    created_at: datetime
    updated_at: datetime


class UserStorageStats(BaseModel):
    user_id: int
    total_files: int = 0
    total_bytes: int = 0
    documents_count: int = 0
    media_count: int = 0
    audio_count: int = 0
    archives_count: int = 0
    other_count: int = 0
