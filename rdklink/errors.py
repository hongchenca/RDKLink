class RdkLinkError(RuntimeError):
    code = "rdklink_error"

class DeviceOfflineError(RdkLinkError): code = "device_offline"
class PermissionDeniedError(RdkLinkError): code = "permission_denied"
class FileTooLargeError(RdkLinkError): code = "file_too_large"
class SerialPortBusyError(RdkLinkError): code = "serial_port_busy"
class ProjectNotFoundError(RdkLinkError): code = "project_not_found"
class ProcessNotFoundError(RdkLinkError): code = "process_not_found"
class TimeoutError(RdkLinkError): code = "timeout"
class InvalidPathError(RdkLinkError): code = "invalid_path"

