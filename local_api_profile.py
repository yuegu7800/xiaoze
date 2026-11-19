"""Explicit local API profile, encrypted for the current Windows user."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile

from core import prepare_api_config


def _crypt(payload, *, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('本机配置加密仅支持Windows；没有保存明文。')

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(payload)
    source = Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                  ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise RuntimeError('无法处理本机加密配置；请在原Windows用户下重新填写。')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


class ApiProfile:
    def __init__(self, directory):
        self.path = Path(directory) / 'api-profile.dpapi'

    def save(self, values):
        cfg = prepare_api_config(values)
        clean = {key: cfg[key] for key in ('endpoint', 'model', 'api_key')}
        if any(len(value) > 4096 for value in clean.values()):
            raise RuntimeError('配置字段过长；没有修改已保存配置。')
        sealed = _crypt(json.dumps(clean, ensure_ascii=False).encode('utf-8'))
        payload = json.dumps({'version': 1, 'protected': base64.b64encode(sealed).decode('ascii')}).encode('ascii')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix='.api-profile-', delete=False) as stream:
                temp = stream.name
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)

    def load(self):
        if not self.path.exists():
            return None
        try:
            if self.path.stat().st_size > 65536:
                raise ValueError()
            envelope = json.loads(self.path.read_bytes())
            if envelope['version'] != 1:
                raise ValueError()
            raw = _crypt(base64.b64decode(envelope['protected'], validate=True), decrypt=True)
            cfg = json.loads(raw)
            if set(cfg) != {'endpoint', 'model', 'api_key'}:
                raise ValueError()
            return prepare_api_config(cfg)
        except (ValueError, KeyError, TypeError, OSError, RuntimeError):
            raise RuntimeError('本机API配置无法读取或解密，原文件保留；请重新填写后保存。') from None

    def forget(self):
        self.path.unlink(missing_ok=True)
