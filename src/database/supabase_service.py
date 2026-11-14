import asyncio
from typing import List, Optional, Dict, Any
from supabase import create_client, Client
from loguru import logger
from src.core.config import settings
from src.database.models import FileMetadata, FileMetadataCreate, FileCategory, UserStorageStats


class DatabaseService:
    def __init__(self):
        self._client: Optional[Client] = None

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = create_client(
                settings.SUPABASE_URL,
                settings.SUPABASE_SERVICE_ROLE_KEY
            )
        return self._client

    async def insert_file(self, file_data: FileMetadataCreate) -> Optional[Dict[str, Any]]:
        try:
            payload = file_data.model_dump()
            payload["category"] = file_data.category.value

            def _query():
                return self.client.table("files").insert(payload).execute()

            res = await asyncio.to_thread(_query)
            if res.data and len(res.data) > 0:
                logger.info(f"Metadata recorded for file: {file_data.file_name} (ID: {res.data[0]['id']})")
                return res.data[0]
            return None
        except Exception as e:
            logger.error(f"Error inserting file metadata: {e}")
            raise

    async def get_file_by_id(self, file_uuid: str) -> Optional[Dict[str, Any]]:
        try:
            def _query():
                return self.client.table("files").select("*").eq("id", file_uuid).execute()

            res = await asyncio.to_thread(_query)
            if res.data and len(res.data) > 0:
                return res.data[0]
            return None
        except Exception as e:
            logger.error(f"Error fetching file by id {file_uuid}: {e}")
            return None

    async def get_user_files(
        self,
        user_id: int,
        category: Optional[FileCategory] = None,
        page: int = 1,
        page_size: int = 5
    ) -> List[Dict[str, Any]]:
        try:
            offset = (page - 1) * page_size
            def _query():
                q = self.client.table("files").select("*").eq("user_id", user_id)
                if category:
                    q = q.eq("category", category.value)
                q = q.order("created_at", desc=True).range(offset, offset + page_size - 1)
                return q.execute()

            res = await asyncio.to_thread(_query)
            return res.data or []
        except Exception as e:
            logger.error(f"Error fetching user files for user {user_id}: {e}")
            return []

    async def count_user_files(
        self,
        user_id: int,
        category: Optional[FileCategory] = None
    ) -> int:
        try:
            def _query():
                q = self.client.table("files").select("id", count="exact").eq("user_id", user_id)
                if category:
                    q = q.eq("category", category.value)
                return q.execute()

            res = await asyncio.to_thread(_query)
            return res.count or 0
        except Exception as e:
            logger.error(f"Error counting files for user {user_id}: {e}")
            return 0

    async def search_files(
        self,
        user_id: int,
        query_text: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        try:
            def _query():
                return self.client.table("files") \
                    .select("*") \
                    .eq("user_id", user_id) \
                    .ilike("file_name", f"%{query_text}%") \
                    .order("created_at", desc=True) \
                    .limit(limit) \
                    .execute()

            res = await asyncio.to_thread(_query)
            return res.data or []
        except Exception as e:
            logger.error(f"Error searching files for user {user_id} with query '{query_text}': {e}")
            return []

    async def delete_file(self, file_uuid: str, user_id: int) -> Optional[Dict[str, Any]]:
        try:
            def _query():
                return self.client.table("files") \
                    .delete() \
                    .eq("id", file_uuid) \
                    .eq("user_id", user_id) \
                    .execute()

            res = await asyncio.to_thread(_query)
            if res.data and len(res.data) > 0:
                logger.info(f"Deleted file {file_uuid} for user {user_id}")
                return res.data[0]
            return None
        except Exception as e:
            logger.error(f"Error deleting file {file_uuid}: {e}")
            return None

    async def get_user_stats(self, user_id: int) -> UserStorageStats:
        try:
            def _query():
                return self.client.table("user_storage_stats").select("*").eq("user_id", user_id).execute()

            res = await asyncio.to_thread(_query)
            if res.data and len(res.data) > 0:
                row = res.data[0]
                return UserStorageStats(
                    user_id=user_id,
                    total_files=row.get("total_files", 0),
                    total_bytes=row.get("total_bytes", 0),
                    documents_count=row.get("documents_count", 0),
                    media_count=row.get("media_count", 0),
                    audio_count=row.get("audio_count", 0),
                    archives_count=row.get("archives_count", 0),
                    other_count=row.get("other_count", 0)
                )
            
            return UserStorageStats(user_id=user_id)
        except Exception as e:
            logger.error(f"Error fetching stats for user {user_id}: {e}")
            return UserStorageStats(user_id=user_id)


db_service = DatabaseService()
