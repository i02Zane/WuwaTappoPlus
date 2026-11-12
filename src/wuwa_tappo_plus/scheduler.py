"""Task Scheduler automation through Windows COM; no shell or dependencies."""

import ctypes as c
from contextlib import contextmanager
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET


class GUID(c.Structure):
    _fields_ = [("data", c.c_ubyte * 16)]


class Value(c.Union):
    _fields_ = [("number", c.c_long), ("pointer", c.c_void_p),
                ("boolean", c.c_short),
                ("storage", c.c_longlong * (2 if c.sizeof(c.c_void_p) == 8 else 1))]


class Variant(c.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("vt", c.c_ushort), ("reserved", c.c_ushort * 3), ("value", Value)]


class Parameters(c.Structure):
    _fields_ = [("args", c.POINTER(Variant)), ("named", c.POINTER(c.c_long)),
                ("count", c.c_uint), ("named_count", c.c_uint)]


class ExceptionInfo(c.Structure):
    _fields_ = [("code", c.c_ushort), ("reserved", c.c_ushort),
                ("source", c.c_void_p), ("description", c.c_void_p),
                ("help_file", c.c_void_p), ("help_context", c.c_ulong),
                ("reserved_pointer", c.c_void_p), ("fill", c.c_void_p), ("scode", c.c_long)]


class SchedulerError(OSError):
    def __init__(self, hresult, message):
        self.hresult = hresult & 0xFFFFFFFF
        super().__init__("%s (0x%08X)" % (message, self.hresult))


def _check(result, message):
    if result < 0:
        raise SchedulerError(result, message)


class Dispatch:
    def __init__(self, pointer, automation):
        self.pointer = c.c_void_p(pointer)
        self.automation = automation

    def _method(self, index, *arguments):
        table = c.cast(self.pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
        return c.WINFUNCTYPE(c.c_long, c.c_void_p, *arguments)(table[index])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._method(2)(self.pointer)  # IUnknown.Release
        self.pointer = None

    def invoke(self, name, flags, *values):
        names = (c.c_wchar_p * 1)(name)
        identifier = c.c_long()
        empty = GUID()
        _check(self._method(5, c.POINTER(GUID), c.POINTER(c.c_wchar_p), c.c_uint,
                            c.c_ulong, c.POINTER(c.c_long))(
                                self.pointer, c.byref(empty), names, 1, 0, c.byref(identifier)), name)
        arguments = (Variant * len(values))()
        result = Variant()
        exception = ExceptionInfo()
        bad_argument = c.c_uint()
        try:
            for item, value in zip(arguments, reversed(values)):
                if value is None:
                    item.vt = 0
                elif isinstance(value, str):
                    item.vt = 8
                    item.pointer = self.automation.SysAllocString(value)
                    if not item.pointer:
                        raise MemoryError()
                elif type(value) is int:
                    item.vt, item.number = 3, value
                else:
                    raise TypeError("unsupported COM argument")
            parameters = Parameters(arguments, None, len(values), 0)
            status = self._method(6, c.c_long, c.POINTER(GUID), c.c_ulong, c.c_ushort,
                                  c.POINTER(Parameters), c.POINTER(Variant),
                                  c.POINTER(ExceptionInfo), c.POINTER(c.c_uint))(
                                      self.pointer, identifier.value, c.byref(empty), 0, flags,
                                      c.byref(parameters), c.byref(result), c.byref(exception),
                                      c.byref(bad_argument))
            if status < 0:
                raise SchedulerError(exception.scode or status, "Task Scheduler: " + name)
            if result.vt == 9:
                pointer, result.vt = result.pointer, 0  # Transfer the owned reference.
                return Dispatch(pointer, self.automation) if pointer else None
            if result.vt == 8:
                return c.wstring_at(result.pointer) if result.pointer else ""
            if result.vt in (2, 3, 22):
                return result.number
            if result.vt == 11:
                return bool(result.boolean)
            if result.vt in (0, 1):
                return None
            raise TypeError("unsupported COM result type: %d" % result.vt)
        finally:
            for item in arguments:
                self.automation.VariantClear(c.byref(item))
            self.automation.VariantClear(c.byref(result))
            for pointer in (exception.source, exception.description, exception.help_file):
                if pointer:
                    self.automation.SysFreeString(pointer)

    def call(self, name, *args):
        return self.invoke(name, 1, *args)

    def get(self, name):
        return self.invoke(name, 2)


@contextmanager
def service():
    ole = c.OleDLL("ole32")
    automation = c.WinDLL("oleaut32")
    automation.SysAllocString.argtypes = [c.c_wchar_p]
    automation.SysAllocString.restype = c.c_void_p
    automation.SysFreeString.argtypes = [c.c_void_p]
    automation.VariantClear.argtypes = [c.POINTER(Variant)]
    ole.CLSIDFromProgID.argtypes = [c.c_wchar_p, c.POINTER(GUID)]
    ole.IIDFromString.argtypes = [c.c_wchar_p, c.POINTER(GUID)]
    ole.CoCreateInstance.argtypes = [c.POINTER(GUID), c.c_void_p, c.c_ulong,
                                   c.POINTER(GUID), c.POINTER(c.c_void_p)]
    # WinDLL lets us distinguish RPC_E_CHANGED_MODE from genuine init failure.
    initialize = c.WinDLL("ole32").CoInitializeEx
    initialize.argtypes = [c.c_void_p, c.c_ulong]
    initialize.restype = c.c_long
    status = initialize(None, 2)
    if status < 0 and status & 0xFFFFFFFF != 0x80010106:
        _check(status, "CoInitializeEx")
    try:
        clsid, iid, pointer = GUID(), GUID(), c.c_void_p()
        ole.CLSIDFromProgID("Schedule.Service", c.byref(clsid))
        ole.IIDFromString("{00020400-0000-0000-C000-000000000046}", c.byref(iid))
        ole.CoCreateInstance(c.byref(clsid), None, 1, c.byref(iid), c.byref(pointer))
        with Dispatch(pointer.value, automation) as scheduler:
            scheduler.call("Connect")
            yield scheduler
    finally:
        if status >= 0:
            ole.CoUninitialize()


def _missing(error):
    return isinstance(error, SchedulerError) and error.hresult in (0x80070002, 0x80070003)


def read(folder, name):
    with service() as scheduler:
        try:
            with scheduler.call("GetFolder", folder) as directory:
                with directory.call("GetTask", name) as task:
                    return {"state": task.get("State")}
        except SchedulerError as exc:
            if _missing(exc):
                return None
            raise


def remove(folder, name):
    with service() as scheduler:
        try:
            with scheduler.call("GetFolder", folder) as directory:
                directory.call("DeleteTask", name, 0)
        except SchedulerError as exc:
            if not _missing(exc):
                raise
    return True


def task_xml(executable, arguments, cwd, minutes, user=None):
    root = ET.Element("Task", xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task", version="1.2")
    trigger = ET.SubElement(ET.SubElement(root, "Triggers"), "TimeTrigger")
    repetition = ET.SubElement(trigger, "Repetition")
    ET.SubElement(repetition, "Interval").text = "PT%dM" % minutes
    ET.SubElement(repetition, "Duration").text = "P3650D"
    ET.SubElement(trigger, "StartBoundary").text = (datetime.now().astimezone() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    principal = ET.SubElement(ET.SubElement(root, "Principals"), "Principal", id="Author")
    if user:
        ET.SubElement(principal, "UserId").text = user
    ET.SubElement(principal, "LogonType").text = "InteractiveToken"
    ET.SubElement(principal, "RunLevel").text = "LeastPrivilege" if user else "HighestAvailable"
    settings = ET.SubElement(root, "Settings")
    for key, value in (("MultipleInstancesPolicy", "IgnoreNew"), ("DisallowStartIfOnBatteries", "false"),
                       ("StopIfGoingOnBatteries", "false"), ("ExecutionTimeLimit", "PT1M")):
        ET.SubElement(settings, key).text = value
    action = ET.SubElement(ET.SubElement(root, "Actions", Context="Author"), "Exec")
    for key, value in (("Command", executable), ("Arguments", arguments), ("WorkingDirectory", cwd)):
        ET.SubElement(action, key).text = str(value)
    return ET.tostring(root, encoding="unicode")


def register(folder, name, executable, arguments, cwd, minutes, user=None):
    xml = task_xml(executable, arguments, cwd, minutes, user)
    with service() as scheduler:
        try:
            directory = scheduler.call("GetFolder", folder)
        except SchedulerError as exc:
            if not _missing(exc):
                raise
            with scheduler.call("GetFolder", "\\") as root:
                directory = root.call("CreateFolder", folder, None)
        with directory:
            with directory.call("RegisterTask", name, xml, 6, user, None, 3, None):
                pass
    return True
