def format_bytes(size: int) -> str:
    """
    Formats a byte integer into a readable human-friendly string (KB, MB, GB).
    """
    if size < 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def get_category_emoji(category: str) -> str:
    """
    Returns an appropriate emoji icon for a file category.
    """
    mapping = {
        "documents": "📄",
        "media": "🎬",
        "audio": "🎵",
        "archives": "📦",
        "other": "📁"
    }
    return mapping.get(category.lower(), "📁")


def get_category_display_name(category: str) -> str:
    """
    Returns localized Arabic / English display name for category.
    """
    mapping = {
        "documents": "مستندات (Documents)",
        "media": "وسائط ومرئيات (Media)",
        "audio": "صوتيات (Audio)",
        "archives": "ملفات مضغوطة (Archives)",
        "other": "أخرى (Other)"
    }
    return mapping.get(category.lower(), category)
