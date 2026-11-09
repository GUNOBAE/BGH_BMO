"""Explicit unavailable cloud capability; never fabricates a model response."""
from .cloud import CloudError


class UnavailableCloudClient:
    def __init__(self, code='credentials_missing'):
        self.code = code if code in ('credentials_missing', 'unsafe_directory', 'unsafe_file',
            'invalid_store', 'store_unavailable', 'credentials_busy', 'missing_key', 'missing_sdk') else 'credentials_unavailable'
        self.responses = self.audio = self.transcriptions = self

    def create(self, **_):
        raise CloudError(self.code, 'Cloud conversation is unavailable. Apply configuration and restart the candidate.')

    def close(self):
        pass
