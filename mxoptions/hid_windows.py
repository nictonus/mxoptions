"""HID devices on Windows (see hidpp.py, which talks through them).

Windows shows every top-level collection of a device as a device of its own. HID++ is in vendor
collections: on a receiver one for the short reports (ID 0x10, 7 bytes) and one for the long ones
(ID 0x11, 20 bytes), on a mouse connected over Bluetooth a single one for the long reports. A
receiver's own messages come as short reports, so both collections are listened to; everything is
sent as long reports.

The collection of a mouse's ordinary reports (movement, buttons, wheel) belongs to Windows alone
and cannot be read along."""

import ctypes
import ctypes.wintypes as w
import queue
import threading

LOGITECH_VID = 0x046D
SHORT_LEN, LONG_LEN = 7, 20
_ACCESS_READ, _ACCESS_WRITE, _SHARE, _OPEN_EXISTING, _OVERLAPPED = 0x80000000, 0x40000000, 0x3, 3, 0x40000000
_IO_PENDING = 997
_short_of = {}  # the path of a long-report collection -> the path of the short-report one beside it

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
INVALID = w.HANDLE(-1).value


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", w.DWORD), ("Data2", w.WORD), ("Data3", w.WORD), ("Data4", ctypes.c_ubyte * 8)]


class _INTERFACE(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("InterfaceClassGuid", _GUID), ("Flags", w.DWORD), ("Reserved", ctypes.c_void_p)]


class _ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", w.ULONG), ("VendorID", w.USHORT), ("ProductID", w.USHORT), ("VersionNumber", w.USHORT)]


class _CAPS(ctypes.Structure):
    _fields_ = [("Usage", w.USHORT), ("UsagePage", w.USHORT), ("InputReportByteLength", w.USHORT),
                ("OutputReportByteLength", w.USHORT), ("FeatureReportByteLength", w.USHORT), ("Reserved", w.USHORT * 17),
                ("Counts", w.USHORT * 10)]


class _OVERLAP(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t), ("Offset", w.DWORD), ("OffsetHigh", w.DWORD),
                ("hEvent", w.HANDLE)]


setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(_GUID), w.LPCWSTR, w.HWND, w.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_GUID), w.DWORD, ctypes.POINTER(_INTERFACE)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_INTERFACE), ctypes.c_void_p, w.DWORD,
                                                      ctypes.POINTER(w.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
kernel32.CreateFileW.restype = w.HANDLE
kernel32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
kernel32.CloseHandle.argtypes = [w.HANDLE]
kernel32.CreateEventW.restype = w.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
kernel32.ReadFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(_OVERLAP)]
kernel32.WriteFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(_OVERLAP)]
kernel32.GetOverlappedResult.argtypes = [w.HANDLE, ctypes.POINTER(_OVERLAP), ctypes.POINTER(w.DWORD), w.BOOL]
kernel32.CancelIoEx.argtypes = [w.HANDLE, ctypes.c_void_p]
hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(_GUID)]
hid.HidD_GetAttributes.argtypes = [w.HANDLE, ctypes.POINTER(_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [w.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_CAPS)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidD_GetProductString.argtypes = [w.HANDLE, ctypes.c_void_p, w.ULONG]


def _paths():
    """The device paths of all HID collections that are there."""
    guid = _GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    found = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, 0x12)  # present, by interface
    if found in (None, INVALID):
        return []
    paths, index = [], 0
    try:
        while True:
            entry = _INTERFACE()
            entry.cbSize = ctypes.sizeof(entry)
            if not setupapi.SetupDiEnumDeviceInterfaces(found, None, ctypes.byref(guid), index, ctypes.byref(entry)):
                break
            index += 1
            needed = w.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(found, ctypes.byref(entry), None, 0, ctypes.byref(needed), None)
            if not needed.value:
                continue
            detail = ctypes.create_string_buffer(needed.value)
            ctypes.cast(detail, ctypes.POINTER(w.DWORD))[0] = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6  # its cbSize
            if setupapi.SetupDiGetDeviceInterfaceDetailW(found, ctypes.byref(entry), detail, needed, None, None):
                paths.append(ctypes.wstring_at(ctypes.addressof(detail) + 4))
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(found)
    return paths


def _describe(path):
    """(vendor, usage page, usage, input report length, product name) of a collection, or None."""
    handle = kernel32.CreateFileW(path, 0, _SHARE, None, _OPEN_EXISTING, 0, None)  # no access asked for: only to look
    if handle == INVALID:
        return None
    try:
        attributes = _ATTRIBUTES()
        attributes.Size = ctypes.sizeof(attributes)
        parsed, caps = ctypes.c_void_p(), _CAPS()
        if not hid.HidD_GetAttributes(handle, ctypes.byref(attributes)) or not hid.HidD_GetPreparsedData(handle, ctypes.byref(parsed)):
            return None
        hid.HidP_GetCaps(parsed, ctypes.byref(caps))
        hid.HidD_FreePreparsedData(parsed)
        name = ctypes.create_unicode_buffer(128)
        hid.HidD_GetProductString(handle, name, ctypes.sizeof(name))
        return attributes.VendorID, caps.UsagePage, caps.Usage, caps.InputReportByteLength, name.value
    finally:
        kernel32.CloseHandle(handle)


def _interface(path):
    """What two collections of one interface of one device have in common in their paths."""
    return path.lower().split("&col")[0]


def find_hidpp_nodes():
    """Return [(device path, is_receiver)] for the Logitech collections that carry HID++ long reports."""
    nodes, shorts = [], {}
    for path in _paths():
        lowered = path.lower()
        if "046d" not in lowered:
            continue
        about = _describe(path)
        if about is None or about[0] != LOGITECH_VID or about[1] not in (0xFF00, 0xFF43):
            continue
        _vendor, _page, _usage, length, name = about
        if length == SHORT_LEN:
            shorts[_interface(path)] = path
        elif length == LONG_LEN:
            usb = lowered.startswith("\\\\?\\hid#vid_")  # a Bluetooth device is named after its HID service instead
            nodes.append((path, usb and "receiver" in name.lower()))
    _short_of.clear()
    for path, _is_receiver in nodes:
        if _interface(path) in shorts:
            _short_of[path] = shorts[_interface(path)]
    return nodes


def find_mouse_report_node(hidpp_path):
    return None


def read_descriptor(node):
    return b""


def short_name(path):
    return path.lower().split("#")[1][:40] if "#" in path else path[:40]


class Port:
    """The long-report collection at ``path``, and the short-report one beside it if there is one. ``read()``
    waits for the next report from either and gives b"" once the port is closed or the device is gone;
    ``write()`` raises OSError if the report cannot be sent."""

    def __init__(self, path, write=True):
        self._handles = []
        self._reports = queue.Queue()
        self._closed = False
        self._write_lock = threading.Lock()
        for index, one in enumerate(filter(None, (path, _short_of.get(path)))):
            access = _ACCESS_READ | (_ACCESS_WRITE if write and index == 0 else 0)
            handle = kernel32.CreateFileW(one, access, _SHARE, None, _OPEN_EXISTING, _OVERLAPPED, None)
            if handle == INVALID:
                error = ctypes.get_last_error()
                if index == 0:
                    raise OSError(error, ctypes.FormatError(error).strip())
                continue  # the receiver's own messages will not be heard; the mouse's still will
            self._handles.append(handle)
            threading.Thread(target=self._listen, args=(handle, LONG_LEN if index == 0 else SHORT_LEN),
                             name="hid-read", daemon=True).start()

    def _listen(self, handle, length):
        event = kernel32.CreateEventW(None, True, False, None)
        buffer = ctypes.create_string_buffer(max(length, 64))
        try:
            while not self._closed:
                overlap, got = _OVERLAP(), w.DWORD()
                overlap.hEvent = event
                done = kernel32.ReadFile(handle, buffer, length, ctypes.byref(got), ctypes.byref(overlap))
                if not done:
                    if ctypes.get_last_error() != _IO_PENDING:
                        break
                    if not kernel32.GetOverlappedResult(handle, ctypes.byref(overlap), ctypes.byref(got), True):
                        break  # cancelled by close(), or the device is gone
                if got.value:
                    self._reports.put(buffer.raw[:got.value])
        finally:
            kernel32.CloseHandle(event)
            self._reports.put(b"")

    def read(self):
        return b"" if self._closed else self._reports.get()

    def write(self, packet):
        if self._closed:
            raise OSError("the device is closed")
        with self._write_lock:
            event = kernel32.CreateEventW(None, True, False, None)
            try:
                overlap, sent = _OVERLAP(), w.DWORD()
                overlap.hEvent = event
                if not kernel32.WriteFile(self._handles[0], bytes(packet), len(packet), ctypes.byref(sent), ctypes.byref(overlap)):
                    error = ctypes.get_last_error()
                    if error != _IO_PENDING or not kernel32.GetOverlappedResult(self._handles[0], ctypes.byref(overlap), ctypes.byref(sent), True):
                        error = ctypes.get_last_error()
                        raise OSError(error, ctypes.FormatError(error).strip())
            finally:
                kernel32.CloseHandle(event)

    def wake(self):
        """Make a waiting read() return (called before close(), from another thread)."""
        self._closed = True
        for handle in self._handles:
            kernel32.CancelIoEx(handle, None)
        self._reports.put(b"")

    def close(self):
        if not self._closed:
            self.wake()
        handles, self._handles = self._handles, []
        for handle in handles:
            kernel32.CloseHandle(handle)
