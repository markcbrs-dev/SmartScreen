import base64
import hashlib
import json
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.db.models.fields.files import FieldFile


def _get_raw_data_key() -> str:
    return getattr(settings, 'SMARTSCREEN_DATA_KEY', '') or settings.SECRET_KEY


def _build_fernet_key(raw_key: str) -> bytes:
    digest = hashlib.sha256(raw_key.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def get_data_cipher() -> Fernet:
    return Fernet(_build_fernet_key(_get_raw_data_key()))


def encrypt_text(value: str) -> str:
    if value is None or value == '':
        return value
    return get_data_cipher().encrypt(value.encode('utf-8')).decode('utf-8')


def decrypt_text(value: str) -> str:
    if value is None or value == '':
        return value
    if not isinstance(value, str):
        return value
    try:
        return get_data_cipher().decrypt(value.encode('utf-8')).decode('utf-8')
    except (InvalidToken, ValueError, TypeError):
        return value


def encrypt_bytes(value: bytes) -> bytes:
    if not value:
        return value
    return get_data_cipher().encrypt(value)


def decrypt_bytes(value: bytes) -> bytes:
    if not value:
        return value
    try:
        return get_data_cipher().decrypt(value)
    except (InvalidToken, ValueError, TypeError):
        return value


def encrypt_json(value) -> str:
    if value is None:
        return None
    return encrypt_text(json.dumps(value, separators=(',', ':')))


def decrypt_json(value, default):
    if value in (None, ''):
        return default
    if isinstance(value, (dict, list)):
        return value
    decrypted = decrypt_text(value)
    try:
        return json.loads(decrypted)
    except (TypeError, json.JSONDecodeError):
        return default


class EncryptedTextField(models.TextField):
    def from_db_value(self, value, expression, connection):
        return decrypt_text(value)

    def to_python(self, value):
        if value is None:
            return value
        if isinstance(value, str):
            return decrypt_text(value)
        return value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ''):
            return value
        return encrypt_text(str(value))


class EncryptedJSONField(models.TextField):
    def __init__(self, *args, **kwargs):
        self.json_default = kwargs.get('default', list)
        super().__init__(*args, **kwargs)

    def _get_default_value(self):
        default = self.json_default
        return default() if callable(default) else default

    def from_db_value(self, value, expression, connection):
        return decrypt_json(value, self._get_default_value())

    def to_python(self, value):
        if value is None:
            return value
        if isinstance(value, (list, dict)):
            return value
        return decrypt_json(value, self._get_default_value())

    def get_prep_value(self, value):
        if value is None:
            return None
        return encrypt_json(value)


class EncryptedResumeStorage(FileSystemStorage):
    def _save(self, name, content):
        if hasattr(content, 'seek'):
            content.seek(0)
        raw_bytes = content.read()
        if isinstance(raw_bytes, str):
            raw_bytes = raw_bytes.encode('utf-8')
        return super()._save(name, ContentFile(encrypt_bytes(raw_bytes)))

    def open(self, name, mode='rb'):
        encrypted_file = super().open(name, 'rb')
        try:
            plaintext = decrypt_bytes(encrypted_file.read())
        finally:
            encrypted_file.close()

        if 'b' in mode:
            return ContentFile(plaintext, name=os.path.basename(name))
        return ContentFile(plaintext.decode('utf-8'), name=os.path.basename(name))


class EncryptedResumeFileField(models.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('storage', EncryptedResumeStorage())
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs.pop('storage', None)
        return name, path, args, kwargs

    def from_db_value(self, value, expression, connection):
        return decrypt_text(value)

    def to_python(self, value):
        if value is None or isinstance(value, FieldFile):
            return value
        if hasattr(value, 'read'):
            return value
        if isinstance(value, str):
            return decrypt_text(value)
        return super().to_python(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ''):
            return value
        return encrypt_text(value)