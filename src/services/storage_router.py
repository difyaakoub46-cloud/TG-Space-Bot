import mimetypes
from pathlib import Path
from typing import Optional, Tuple
from aiogram.types import Message, Document, Audio, Voice, Video, PhotoSize, Animation
from src.core.config import settings
from src.database.models import FileCategory


EXTENSIONS_MAP = {
    ".pdf": FileCategory.DOCUMENTS,
    ".doc": FileCategory.DOCUMENTS,
    ".docx": FileCategory.DOCUMENTS,
    ".xls": FileCategory.DOCUMENTS,
    ".xlsx": FileCategory.DOCUMENTS,
    ".ppt": FileCategory.DOCUMENTS,
    ".pptx": FileCategory.DOCUMENTS,
    ".txt": FileCategory.DOCUMENTS,
    ".csv": FileCategory.DOCUMENTS,
    ".epub": FileCategory.DOCUMENTS,
    ".rtf": FileCategory.DOCUMENTS,
    ".jpg": FileCategory.MEDIA,
    ".jpeg": FileCategory.MEDIA,
    ".png": FileCategory.MEDIA,
    ".webp": FileCategory.MEDIA,
    ".gif": FileCategory.MEDIA,
    ".mp4": FileCategory.MEDIA,
    ".mkv": FileCategory.MEDIA,
    ".mov": FileCategory.MEDIA,
    ".avi": FileCategory.MEDIA,
    ".webm": FileCategory.MEDIA,
    ".mp3": FileCategory.AUDIO,
    ".wav": FileCategory.AUDIO,
    ".ogg": FileCategory.AUDIO,
    ".flac": FileCategory.AUDIO,
    ".m4a": FileCategory.AUDIO,
    ".aac": FileCategory.AUDIO,
    ".zip": FileCategory.ARCHIVES,
    ".rar": FileCategory.ARCHIVES,
    ".7z": FileCategory.ARCHIVES,
    ".tar": FileCategory.ARCHIVES,
    ".gz": FileCategory.ARCHIVES,
    ".bz2": FileCategory.ARCHIVES,
    ".xz": FileCategory.ARCHIVES,
    ".iso": FileCategory.ARCHIVES,
}


class StorageRouter:
    @classmethod
    def detect_category(
        cls,
        file_name: Optional[str] = None,
        mime_type: Optional[str] = None,
        is_video: bool = False,
        is_photo: bool = False,
        is_audio: bool = False,
        is_voice: bool = False,
        is_animation: bool = False
    ) -> FileCategory:
        if is_video or is_photo or is_animation:
            return FileCategory.MEDIA
        if is_audio or is_voice:
            return FileCategory.AUDIO

        if file_name:
            ext = Path(file_name).suffix.lower()
            if ext in EXTENSIONS_MAP:
                return EXTENSIONS_MAP[ext]

        if mime_type:
            mime_lower = mime_type.lower()
            if mime_lower.startswith("video/") or mime_lower.startswith("image/"):
                return FileCategory.MEDIA
            if mime_lower.startswith("audio/"):
                return FileCategory.AUDIO
            if any(arch in mime_lower for arch in ["zip", "rar", "7z", "tar", "compressed", "x-bzip", "x-gzip"]):
                return FileCategory.ARCHIVES
            if any(doc in mime_lower for doc in ["pdf", "msword", "officedocument", "text/"]):
                return FileCategory.DOCUMENTS

        if file_name:
            guessed_mime, _ = mimetypes.guess_type(file_name)
            if guessed_mime:
                if guessed_mime.startswith("video/") or guessed_mime.startswith("image/"):
                    return FileCategory.MEDIA
                if guessed_mime.startswith("audio/"):
                    return FileCategory.AUDIO
                if guessed_mime.startswith("text/") or "pdf" in guessed_mime:
                    return FileCategory.DOCUMENTS

        return FileCategory.OTHER

    @classmethod
    def get_destination_chat_id(cls, category: FileCategory) -> int:
        mapping = {
            FileCategory.DOCUMENTS: settings.STORAGE_DOCUMENTS_CHAT_ID,
            FileCategory.MEDIA: settings.STORAGE_MEDIA_CHAT_ID,
            FileCategory.AUDIO: settings.STORAGE_AUDIO_CHAT_ID,
            FileCategory.ARCHIVES: settings.STORAGE_ARCHIVES_CHAT_ID,
            FileCategory.OTHER: settings.STORAGE_DEFAULT_CHAT_ID,
        }
        return mapping.get(category, settings.STORAGE_DEFAULT_CHAT_ID)

    @classmethod
    def parse_message_file(cls, message: Message) -> Optional[Tuple[str, str, str, int, Optional[str], FileCategory, int]]:
        file_id = None
        file_unique_id = None
        file_name = "unknown_file"
        file_size = 0
        mime_type = None
        category = FileCategory.OTHER

        if message.document:
            doc: Document = message.document
            file_id = doc.file_id
            file_unique_id = doc.file_unique_id
            file_name = doc.file_name or "document"
            file_size = doc.file_size or 0
            mime_type = doc.mime_type
            category = cls.detect_category(file_name=file_name, mime_type=mime_type)

        elif message.video:
            vid: Video = message.video
            file_id = vid.file_id
            file_unique_id = vid.file_unique_id
            file_name = vid.file_name or f"video_{file_unique_id}.mp4"
            file_size = vid.file_size or 0
            mime_type = vid.mime_type or "video/mp4"
            category = cls.detect_category(file_name=file_name, mime_type=mime_type, is_video=True)

        elif message.photo:
            photo: PhotoSize = message.photo[-1]
            file_id = photo.file_id
            file_unique_id = photo.file_unique_id
            file_name = f"photo_{file_unique_id}.jpg"
            file_size = photo.file_size or 0
            mime_type = "image/jpeg"
            category = cls.detect_category(file_name=file_name, mime_type=mime_type, is_photo=True)

        elif message.audio:
            aud: Audio = message.audio
            file_id = aud.file_id
            file_unique_id = aud.file_unique_id
            file_name = aud.file_name or f"audio_{file_unique_id}.mp3"
            file_size = aud.file_size or 0
            mime_type = aud.mime_type or "audio/mpeg"
            category = cls.detect_category(file_name=file_name, mime_type=mime_type, is_audio=True)

        elif message.voice:
            voice: Voice = message.voice
            file_id = voice.file_id
            file_unique_id = voice.file_unique_id
            file_name = f"voice_{file_unique_id}.ogg"
            file_size = voice.file_size or 0
            mime_type = voice.mime_type or "audio/ogg"
            category = cls.detect_category(file_name=file_name, mime_type=mime_type, is_voice=True)

        elif message.animation:
            anim: Animation = message.animation
            file_id = anim.file_id
            file_unique_id = anim.file_unique_id
            file_name = anim.file_name or f"animation_{file_unique_id}.mp4"
            file_size = anim.file_size or 0
            mime_type = anim.mime_type or "video/mp4"
            category = cls.detect_category(file_name=file_name, mime_type=mime_type, is_animation=True)

        if not file_id:
            return None

        destination_chat_id = cls.get_destination_chat_id(category)
        return file_id, file_unique_id, file_name, file_size, mime_type, category, destination_chat_id
