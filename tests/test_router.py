from src.database.models import FileCategory
from src.services.storage_router import StorageRouter


def test_category_detection():
    assert StorageRouter.detect_category("document.pdf") == FileCategory.DOCUMENTS
    assert StorageRouter.detect_category("sheet.xlsx") == FileCategory.DOCUMENTS
    assert StorageRouter.detect_category("notes.txt") == FileCategory.DOCUMENTS
    assert StorageRouter.detect_category("photo.jpg") == FileCategory.MEDIA
    assert StorageRouter.detect_category("video.mp4") == FileCategory.MEDIA
    assert StorageRouter.detect_category(is_photo=True) == FileCategory.MEDIA
    assert StorageRouter.detect_category(is_video=True) == FileCategory.MEDIA
    assert StorageRouter.detect_category("song.mp3") == FileCategory.AUDIO
    assert StorageRouter.detect_category(is_audio=True) == FileCategory.AUDIO
    assert StorageRouter.detect_category(is_voice=True) == FileCategory.AUDIO
    assert StorageRouter.detect_category("archive.zip") == FileCategory.ARCHIVES
    assert StorageRouter.detect_category("data.tar.gz") == FileCategory.ARCHIVES
    assert StorageRouter.detect_category("backup.7z") == FileCategory.ARCHIVES
    assert StorageRouter.detect_category("bundle.rar") == FileCategory.ARCHIVES


def test_mime_detection():
    assert StorageRouter.detect_category(mime_type="application/pdf") == FileCategory.DOCUMENTS
    assert StorageRouter.detect_category(mime_type="image/png") == FileCategory.MEDIA
    assert StorageRouter.detect_category(mime_type="audio/ogg") == FileCategory.AUDIO
    assert StorageRouter.detect_category(mime_type="application/zip") == FileCategory.ARCHIVES
