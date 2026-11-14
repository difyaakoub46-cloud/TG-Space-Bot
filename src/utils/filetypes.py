"""File classification: categories, MIME/extension detection and Telegram extraction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from aiogram.types import Message


class FileCategory(str, Enum):
    """Storage buckets a file can be routed to."""

    DOCUMENT = "document"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    ARCHIVE = "archive"
    OTHER = "other"


CATEGORY_LABELS: dict[FileCategory, str] = {
    FileCategory.DOCUMENT: "📄 مستندات",
    FileCategory.IMAGE: "🖼 صور",
    FileCategory.VIDEO: "🎬 فيديو",
    FileCategory.AUDIO: "🎧 صوتيات",
    FileCategory.ARCHIVE: "🗜 ملفات مضغوطة",
    FileCategory.OTHER: "📦 أخرى",
}


def parse_category(value: str | None) -> FileCategory:
    """Best-effort conversion of a raw string into a :class:`FileCategory`."""
    if not value:
        return FileCategory.OTHER
    try:
        return FileCategory(value.strip().lower())
    except ValueError:
        return FileCategory.OTHER


ARCHIVE_EXTENSIONS = frozenset(
    {
        "zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "tbz2", "xz", "txz",
        "zst", "lz", "lzma", "cab", "iso", "jar", "war", "apk", "ipa", "dmg",
        "rpm", "deb",
    }
)
IMAGE_EXTENSIONS = frozenset(
    {
        "jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "svg",
        "heic", "heif", "avif", "raw", "cr2", "nef", "jfif",
    }
)
VIDEO_EXTENSIONS = frozenset(
    {
        "mp4", "mkv", "avi", "mov", "m4v", "webm", "flv", "wmv", "mpeg",
        "mpg", "3gp", "ts", "m2ts", "ogv",
    }
)
AUDIO_EXTENSIONS = frozenset(
    {
        "mp3", "wav", "flac", "aac", "ogg", "oga", "m4a", "opus", "wma",
        "aiff", "alac", "amr", "mid", "midi",
    }
)
DOCUMENT_EXTENSIONS = frozenset(
    {
        "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "rtf",
        "odt", "ods", "odp", "csv", "tsv", "md", "json", "xml", "yaml", "yml",
        "html", "htm", "epub", "mobi", "djvu", "tex", "log", "ini", "cfg",
        "sql", "py", "js", "ts", "jsx", "tsx", "java", "c", "cpp", "h", "cs",
        "go", "rs", "rb", "php", "sh", "ps1", "ipynb",
    }
)
ARCHIVE_MIME_TYPES = frozenset(
    {
        "application/zip",
        "application/x-zip-compressed",
        "application/x-rar-compressed",
        "application/vnd.rar",
        "application/x-7z-compressed",
        "application/x-tar",
        "application/gzip",
        "application/x-gzip",
        "application/x-bzip2",
        "application/x-xz",
        "application/x-iso9660-image",
        "application/java-archive",
    }
)

_ARCHIVE_MIME_SUFFIXES = ("zip", "rar", "7z", "tar", "gzip", "bzip2", "x-xz", "compressed")


def extension_of(file_name: str | None) -> str:
    base = (file_name or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in base:
        return ""
    return base.rsplit(".", 1)[-1].lower()[:10]


def detect_category(
    *,
    file_name: str | None = None,
    mime_type: str | None = None,
    content_type: str | None = None,
) -> FileCategory:
    """Classify a file using Telegram's content type, its MIME type and its extension."""
    extension = extension_of(file_name)
    mime = (mime_type or "").strip().lower()

    # Archives win over everything: a ZIP named .docx is still an archive.
    if extension in ARCHIVE_EXTENSIONS:
        return FileCategory.ARCHIVE
    if mime in ARCHIVE_MIME_TYPES or mime.endswith(_ARCHIVE_MIME_SUFFIXES):
        return FileCategory.ARCHIVE

    if content_type == "photo":
        return FileCategory.IMAGE
    if content_type in {"video", "animation", "video_note"}:
        return FileCategory.VIDEO
    if content_type in {"audio", "voice"}:
        return FileCategory.AUDIO

    if mime.startswith("image/"):
        return FileCategory.IMAGE
    if mime.startswith("video/"):
        return FileCategory.VIDEO
    if mime.startswith("audio/"):
        return FileCategory.AUDIO

    if extension in IMAGE_EXTENSIONS:
        return FileCategory.IMAGE
    if extension in VIDEO_EXTENSIONS:
        return FileCategory.VIDEO
    if extension in AUDIO_EXTENSIONS:
        return FileCategory.AUDIO
    if extension in DOCUMENT_EXTENSIONS or mime.startswith(("text/", "application/")):
        return FileCategory.DOCUMENT

    return FileCategory.OTHER


@dataclass(frozen=True, slots=True)
class FileCandidate:
    """Normalized description of an incoming Telegram file."""

    file_id: str
    file_unique_id: str | None
    file_name: str
    mime_type: str | None
    size_bytes: int
    category: FileCategory
    content_type: str


def _timestamp(date: datetime | None) -> str:
    return (date or datetime.utcnow()).strftime("%Y%m%d_%H%M%S")


def extract_candidate(message: Message) -> FileCandidate | None:
    """Turn any supported Telegram message into a :class:`FileCandidate`."""
    document = message.document
    if document is not None:
        return FileCandidate(
            file_id=document.file_id,
            file_unique_id=document.file_unique_id,
            file_name=document.file_name or f"document_{_timestamp(message.date)}",
            mime_type=document.mime_type,
            size_bytes=int(document.file_size or 0),
            category=detect_category(
                file_name=document.file_name,
                mime_type=document.mime_type,
                content_type="document",
            ),
            content_type="document",
        )

    photo = message.photo[-1] if message.photo else None
    if photo is not None:
        return FileCandidate(
            file_id=photo.file_id,
            file_unique_id=photo.file_unique_id,
            file_name=f"photo_{_timestamp(message.date)}.jpg",
            mime_type="image/jpeg",
            size_bytes=int(photo.file_size or 0),
            category=FileCategory.IMAGE,
            content_type="photo",
        )

    if message.video is not None:
        video = message.video
        return FileCandidate(
            file_id=video.file_id,
            file_unique_id=video.file_unique_id,
            file_name=video.file_name or f"video_{_timestamp(message.date)}.mp4",
            mime_type=video.mime_type,
            size_bytes=int(video.file_size or 0),
            category=FileCategory.VIDEO,
            content_type="video",
        )

    if message.animation is not None:
        animation = message.animation
        return FileCandidate(
            file_id=animation.file_id,
            file_unique_id=animation.file_unique_id,
            file_name=animation.file_name or f"animation_{_timestamp(message.date)}.mp4",
            mime_type=animation.mime_type or "video/mp4",
            size_bytes=int(animation.file_size or 0),
            category=FileCategory.VIDEO,
            content_type="animation",
        )

    if message.video_note is not None:
        note = message.video_note
        return FileCandidate(
            file_id=note.file_id,
            file_unique_id=note.file_unique_id,
            file_name=f"video_note_{_timestamp(message.date)}.mp4",
            mime_type="video/mp4",
            size_bytes=int(note.file_size or 0),
            category=FileCategory.VIDEO,
            content_type="video_note",
        )

    if message.audio is not None:
        audio = message.audio
        return FileCandidate(
            file_id=audio.file_id,
            file_unique_id=audio.file_unique_id,
            file_name=audio.file_name or f"{audio.title or 'audio'}_{_timestamp(message.date)}.mp3",
            mime_type=audio.mime_type,
            size_bytes=int(audio.file_size or 0),
            category=FileCategory.AUDIO,
            content_type="audio",
        )

    if message.voice is not None:
        voice = message.voice
        return FileCandidate(
            file_id=voice.file_id,
            file_unique_id=voice.file_unique_id,
            file_name=f"voice_{_timestamp(message.date)}.ogg",
            mime_type=voice.mime_type or "audio/ogg",
            size_bytes=int(voice.file_size or 0),
            category=FileCategory.AUDIO,
            content_type="voice",
        )

    return None
