"""Set/read the documented AppUserModelID property on a Windows shortcut."""
from __future__ import annotations

import ctypes
from uuid import UUID


def set_shortcut_app_id(path, app_id):
    class Guid(ctypes.Structure):
        _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                    ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]
        @classmethod
        def value(cls, text):
            return cls.from_buffer_copy(UUID(text).bytes_le)
    class PropertyKey(ctypes.Structure):
        _fields_ = [("fmtid", Guid), ("pid", ctypes.c_uint32)]
    class Value(ctypes.Union):
        _fields_ = [("pointer", ctypes.c_void_p), ("space", ctypes.c_ubyte * 16)]
    class Variant(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ushort), ("reserved", ctypes.c_ushort * 3), ("value", Value)]

    ole = ctypes.OleDLL("ole32")
    shell = ctypes.OleDLL("shell32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole.CoInitializeEx.restype = ctypes.c_long
    initialized = False
    try:
        hr = ole.CoInitializeEx(None, 2)
        initialized = hr in (0, 1)
    except OSError as error:
        # Existing COM apartment is usable; do not uninitialize its owner.
        if getattr(error, "winerror", 0) & 0xffffffff != 0x80010106:
            raise
    iid = Guid.value("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")
    key = PropertyKey(Guid.value("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3"), 5)
    store = ctypes.c_void_p()
    acquire = shell.SHGetPropertyStoreFromParsingName
    acquire.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32,
                        ctypes.POINTER(Guid), ctypes.POINTER(ctypes.c_void_p)]
    acquire.restype = ctypes.c_long
    try:
        acquire(str(path), None, 2, ctypes.byref(iid), ctypes.byref(store))
        table = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        def method(index, *types):
            return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[index])
        buffer = ctypes.create_unicode_buffer(app_id)
        variant = Variant()
        variant.type = 31  # VT_LPWSTR
        variant.value.pointer = ctypes.cast(buffer, ctypes.c_void_p)
        setter = method(6, ctypes.POINTER(PropertyKey), ctypes.POINTER(Variant))
        if setter(store, ctypes.byref(key), ctypes.byref(variant)) < 0 or method(7)(store) < 0:
            raise OSError("product_shortcut_app_id_write_failed")
        readback = Variant()
        try:
            getter = method(5, ctypes.POINTER(PropertyKey), ctypes.POINTER(Variant))
            if getter(store, ctypes.byref(key), ctypes.byref(readback)) < 0 or readback.type != 31:
                raise OSError("product_shortcut_app_id_readback_failed")
            if ctypes.wstring_at(readback.value.pointer) != app_id:
                raise OSError("product_shortcut_app_id_mismatch")
        finally:
            ole.PropVariantClear(ctypes.byref(readback))
    finally:
        if store.value:
            table = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])(store)
        if initialized:
            ole.CoUninitialize()
