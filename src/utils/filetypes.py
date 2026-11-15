"""File-type classification utilities."""

from __future__ import annotations

import mimetypes
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from aiogram.types import Document, Message


MIME_AUDIO = ("audio",)
MIME_VIDEO = ("video",)
MIME_IMAGE = ("image",)
MIME_DOCUMENT = ("application/pdf", "application/msword",
                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                  "application/vnd.ms-excel",
                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                  "application/vnd.ms-powerpoint",
                  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                  "application/zip", "application/x-zip-compressed",
                  "application/x-rar-compressed", "application/gzip",
                  "text/plain", "text/csv", "application/json",
                  "application/xml", "text/html", "text/markdown",)
MIME_ARCHIVE = ("application/zip", "application/x-zip-compressed",
                 "application/x-rar-compressed", "application/gzip",
                 "application/x-tar", "application/x-7z-compressed",)


class FileCategory(str, Enum):
    document = "document"
    image = "image"
    video = "video"
    audio = "audio"
    archive = "archive"
    other = "other"


@dataclass(slots=True)
class FileCandidate:
    file_name: str
    mime_type: str
    category: FileCategory
    file_id: str = ""
    file_unique_id: str | None = None
    file_size: int = 0
    thumbnail_file_id: str | None = None


def classify(mime: str, name: str) -> FileCategory:
    mime = (mime or "").lower()
    name_lower = (os.path.basename(name) or "").lower()

    if mime.startswith(MIME_IMAGE):
        return FileCategory.image
    if mime.startswith(MIME_VIDEO):
        return FileCategory.video
    if mime.startswith(MIME_AUDIO):
        return FileCategory.audio
    if mime in MIME_ARCHIVE:
        return FileCategory.archive
    if mime in MIME_DOCUMENT:
        return FileCategory.document

    ext = os.path.splitext(name_lower)[1]
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
        return FileCategory.image
    if ext in (".mp4", ".webm", ".mov", ".avi", ".mkv"):
        return FileCategory.video
    if ext in (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"):
        return FileCategory.audio
    if ext in (".zip", ".rar", ".7z", ".tar", ".gz"):
        return FileCategory.archive
    if ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"):
        return FileCategory.document

    return FileCategory.other


def parse_category(value: Any) -> FileCategory:
    """Best-effort conversion of a stored category value into a FileCategory."""
    if isinstance(value, FileCategory):
        return value
    try:
        return FileCategory(str(value or "").strip().lower())
    except ValueError:
        return FileCategory.other


def extract_candidate(message: Message) -> FileCandidate | None:
    document = message.document
    photo = message.photo
    video = message.video
    audio = message.audio
    voice = message.voice
    animation = message.animation
    video_note = message.video_note

    if document:
        name = document.file_name or "file"
        mime = document.mime_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        thumb = document.thumb
        return FileCandidate(
            file_name=name,
            mime_type=mime,
            category=classify(mime, name),
            file_id=document.file_id,
            file_unique_id=document.file_unique_id,
            file_size=document.file_size,
            thumbnail_file_id=thumb.file_id if thumb else None,
        )

    if photo:
        last = photo[-1]
        name = f"photo_{last.file_unique_id}.jpg"
        return FileCandidate(
            file_name=name,
            mime_type="image/jpeg",
            category=FileCategory.image,
            file_id=last.file_id,
            file_unique_id=last.file_unique_id,
            file_size=last.file_size,
            thumbnail_file_id=last.file_id,
        )

    if video:
        name = video.file_name or f"video_{video.file_unique_id}.mp4"
        mime = video.mime_type or "video/mp4"
        return FileCandidate(
            file_name=name,
            mime_type=mime,
            category=FileCategory.video,
            file_id=video.file_id,
            file_unique_id=video.file_unique_id,
            file_size=video.file_size,
        )

    if audio:
        name = audio.file_name or f"audio_{audio.file_unique_id}.mp3"
        mime = audio.mime_type or "audio/mpeg"
        return FileCandidate(
            file_name=name,
            mime_type=mime,
            category=FileCategory.audio,
            file_id=audio.file_id,
            file_unique_id=audio.file_unique_id,
            file_size=audio.file_size,
        )

    if voice:
        name = f"voice_{voice.file_unique_id}.ogg"
        return FileCandidate(
            file_name=name,
            mime_type="audio/ogg",
            category=FileCategory.audio,
            file_id=voice.file_id,
            file_unique_id=voice.file_unique_id,
            file_size=voice.file_size,
        )

    if animation:
        name = animation.file_name or f"animation_{animation.file_unique_id}.mp4"
        mime = animation.mime_type or "video/mp4"
        return FileCandidate(
            file_name=name,
            mime_type=mime,
            category=FileCategory.video,
            file_id=animation.file_id,
            file_unique_id=animation.file_unique_id,
            file_size=animation.file_size,
        )

    if video_note:
        name = f"video_note_{video_note.file_unique_id}.mp4"
        return FileCandidate(
            file_name=name,
            mime_type="video/mp4",
            category=FileCategory.video,
            file_id=video_note.file_id,
            file_unique_id=video_note.file_unique_id,
            file_size=video_note.file_size,
        )

    return None
