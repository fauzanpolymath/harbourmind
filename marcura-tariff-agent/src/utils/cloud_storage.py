"""
cloud_storage.py - Google Cloud Storage integration for HarbourMind

Handles:
- Uploading files to Google Cloud Storage
- Saving calculation logs as JSON
- Retrieving and listing logs
- Cleanup of old logs
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any


class CloudStorageManager:
    """
    Manages interactions with Google Cloud Storage bucket.

    In production, this would use google-cloud-storage library:
        from google.cloud import storage
        self.client = storage.Client(project=project_id)
        self.bucket = self.client.bucket(bucket_name)

    For development, provides mock implementation.
    """

    def __init__(self, project_id: str, bucket_name: str, use_mock: bool = True):
        """
        Initialize CloudStorageManager.

        Args:
            project_id: GCP project ID
            bucket_name: GCS bucket name
            use_mock: Use mock storage (for development) vs. real GCS
        """
        self.project_id = project_id
        self.bucket_name = bucket_name
        self.use_mock = use_mock

        if not use_mock:
            try:
                from google.cloud import storage
                self.client = storage.Client(project=project_id)
                self.bucket = self.client.bucket(bucket_name)
            except ImportError:
                raise ImportError(
                    "google-cloud-storage not installed. "
                    "Install with: pip install google-cloud-storage"
                )
        else:
            # Mock storage in-memory
            self._mock_storage: Dict[str, Any] = {}

    def upload_file(
        self,
        file_bytes: bytes,
        file_path: str,
        content_type: str = "application/octet-stream"
    ) -> str:
        """
        Upload file to Cloud Storage.

        Args:
            file_bytes: File content as bytes
            file_path: Path in bucket (e.g., 'pdfs/2026-05-06/tariff.pdf')
            content_type: MIME type of file

        Returns:
            Public URL of uploaded file
        """
        if self.use_mock:
            self._mock_storage[file_path] = {
                'content': file_bytes,
                'content_type': content_type,
                'uploaded': datetime.utcnow().isoformat()
            }
            return f"gs://{self.bucket_name}/{file_path}"
        else:
            try:
                blob = self.bucket.blob(file_path)
                blob.upload_from_string(file_bytes, content_type=content_type)
                return blob.public_url
            except Exception as e:
                raise RuntimeError(f"Failed to upload file: {str(e)}")

    def save_calculation_log(
        self,
        calculation_id: str,
        log_data: Dict[str, Any]
    ) -> str:
        """
        Save calculation log as JSON to Cloud Storage.

        Args:
            calculation_id: Unique calculation identifier
            log_data: Dictionary containing calculation results and metadata

        Returns:
            Path to saved log file in bucket
        """
        timestamp = datetime.utcnow()
        date_str = timestamp.strftime("%Y-%m-%d")

        # Construct path: logs/YYYY-MM-DD/calculation_id.json
        log_path = f"logs/{date_str}/{calculation_id}.json"

        # Add metadata
        log_data['saved_timestamp'] = timestamp.isoformat()
        log_data['bucket'] = self.bucket_name

        # Convert to JSON
        json_content = json.dumps(log_data, indent=2, default=str)
        json_bytes = json_content.encode('utf-8')

        # Upload
        self.upload_file(json_bytes, log_path, content_type="application/json")

        return log_path

    def get_log(self, calculation_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a specific calculation log.

        Args:
            calculation_id: ID to look up

        Returns:
            Log data as dictionary, or None if not found
        """
        # Try to find in recent dates
        for days_back in range(30):  # Search last 30 days
            date = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            log_path = f"logs/{date}/{calculation_id}.json"

            if self.use_mock:
                if log_path in self._mock_storage:
                    content = self._mock_storage[log_path]['content'].decode('utf-8')
                    return json.loads(content)
            else:
                try:
                    blob = self.bucket.blob(log_path)
                    if blob.exists():
                        content = blob.download_as_string().decode('utf-8')
                        return json.loads(content)
                except:
                    pass

        return None

    def list_logs(
        self,
        port: Optional[str] = None,
        vessel: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        List calculation logs with optional filtering.

        Args:
            port: Filter by port name
            vessel: Filter by vessel name
            start_date: Filter from date (YYYY-MM-DD)
            end_date: Filter to date (YYYY-MM-DD)
            limit: Maximum results to return

        Returns:
            List of log metadata (not full content)
        """
        logs = []

        if self.use_mock:
            # Return all mock logs
            for log_path, data in self._mock_storage.items():
                if 'logs/' in log_path:
                    try:
                        content = data['content'].decode('utf-8')
                        log_data = json.loads(content)
                        logs.append(log_data)
                    except:
                        pass
        else:
            # List from GCS
            try:
                blobs = self.bucket.list_blobs(prefix="logs/")
                for blob in blobs:
                    if blob.name.endswith('.json'):
                        try:
                            content = blob.download_as_string().decode('utf-8')
                            log_data = json.loads(content)
                            logs.append(log_data)
                        except:
                            pass
            except Exception as e:
                print(f"Error listing logs: {str(e)}")

        # Apply filters
        filtered_logs = []
        for log in logs:
            if port and log.get('port', '').lower() != port.lower():
                continue
            if vessel and vessel.lower() not in log.get('vessel_name', '').lower():
                continue

            # Date filtering
            if start_date or end_date:
                try:
                    log_date = log.get('timestamp', '').split('T')[0]
                    if start_date and log_date < start_date:
                        continue
                    if end_date and log_date > end_date:
                        continue
                except:
                    pass

            filtered_logs.append(log)

        # Sort by timestamp (newest first) and limit
        filtered_logs.sort(
            key=lambda x: x.get('timestamp', ''),
            reverse=True
        )

        return filtered_logs[:limit]

    def delete_old_logs(self, days: int = 30) -> int:
        """
        Delete logs older than specified days (cleanup).

        Args:
            days: Age threshold in days

        Returns:
            Number of logs deleted
        """
        cutoff_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        deleted_count = 0

        if self.use_mock:
            paths_to_delete = []
            for log_path in self._mock_storage.keys():
                if 'logs/' in log_path:
                    # Extract date from path: logs/YYYY-MM-DD/...
                    try:
                        date_part = log_path.split('/')[1]
                        if date_part < cutoff_date:
                            paths_to_delete.append(log_path)
                    except:
                        pass

            for path in paths_to_delete:
                del self._mock_storage[path]
                deleted_count += 1
        else:
            try:
                blobs = self.bucket.list_blobs(prefix="logs/")
                for blob in blobs:
                    if blob.name.endswith('.json'):
                        try:
                            # Extract date from path
                            date_part = blob.name.split('/')[1]
                            if date_part < cutoff_date:
                                blob.delete()
                                deleted_count += 1
                        except:
                            pass
            except Exception as e:
                print(f"Error deleting old logs: {str(e)}")

        return deleted_count
