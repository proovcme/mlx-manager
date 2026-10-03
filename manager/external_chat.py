"""Optional loopback LaunchAgent adapter; clean installs use managed runtimes."""
import os
import time
import config
import memory

class ExternalChatMixin:
    def _external_listener(self):
        return memory.listener_pid(config.EXTERNAL_PORT) if config.EXTERNAL_ENABLED else None

    def _stop_external(self) -> None:
        if not config.EXTERNAL_ENABLED:
            return
        from core import ManagerError, run
        listener = self._external_listener()
        if not listener:
            return
        if listener != memory.launch_agent_pid(config.EXTERNAL_LABEL):
            raise ManagerError("External chat port is not owned by the configured LaunchAgent")
        if (self._active_chat or memory.established_connections(config.EXTERNAL_PORT) or
                memory.established_connections(config.PROXY_PORT)):
            raise ManagerError("external chat service has active requests")
        uid = os.getuid()
        result = run(["launchctl", "bootout", f"gui/{uid}/{config.EXTERNAL_LABEL}"])
        if result.returncode:
            raise ManagerError(f"Cannot stop external chat service: {result.stderr.strip()}")
        deadline = time.monotonic() + 30
        while self._external_listener() and time.monotonic() < deadline:
            time.sleep(0.3)
        if self._external_listener():
            raise ManagerError("external chat service did not stop within 30 seconds")

    def _start_external(self) -> None:
        from core import ManagerError, run
        if not config.EXTERNAL_ENABLED:
            raise ManagerError("External chat adapter is disabled")
        if not config.EXTERNAL_MODEL.is_dir():
            raise ManagerError("Configured external chat model is unavailable")
        if memory.image_processes():
            raise ManagerError("Image process is still running")
        listener = self._external_listener()
        if listener:
            if listener != memory.launch_agent_pid(config.EXTERNAL_LABEL):
                raise ManagerError("External chat port is occupied by another process")
            self._ensure_proxy()
            return
        uid = os.getuid()
        result = run(["launchctl", "bootstrap", f"gui/{uid}", str(config.EXTERNAL_PLIST)])
        if result.returncode and run(["launchctl", "print",
                                      f"gui/{uid}/{config.EXTERNAL_LABEL}"]).returncode:
            raise ManagerError(f"Cannot bootstrap external chat service: {result.stderr.strip()}")
        result = run(["launchctl", "kickstart", f"gui/{uid}/{config.EXTERNAL_LABEL}"])
        if result.returncode:
            raise ManagerError(f"Cannot start external chat service: {result.stderr.strip()}")
        deadline = time.monotonic() + 180
        while not memory.port_open(config.EXTERNAL_PORT) and time.monotonic() < deadline:
            time.sleep(1)
        if not memory.port_open(config.EXTERNAL_PORT):
            raise ManagerError("external chat service did not become ready within 180 seconds")
        self._ensure_proxy()

    def _ensure_proxy(self) -> None:
        from core import ManagerError, run
        if config.PROXY_ENDPOINT == config.EXTERNAL_ENDPOINT:
            return
        if not config.PROXY_LABEL:
            raise ManagerError("A proxy LaunchAgent label is required")
        listener = memory.listener_pid(config.PROXY_PORT)
        if listener:
            if listener != memory.launch_agent_pid(config.PROXY_LABEL):
                raise ManagerError("Proxy port is occupied by another process")
            return
        uid = os.getuid()
        result = run(["launchctl", "bootstrap", f"gui/{uid}", str(config.PROXY_PLIST)])
        if result.returncode and run(["launchctl", "print",
                                      f"gui/{uid}/{config.PROXY_LABEL}"]).returncode:
            raise ManagerError(f"Cannot bootstrap proxy: {result.stderr.strip()}")
        result = run(["launchctl", "kickstart", f"gui/{uid}/{config.PROXY_LABEL}"])
        if result.returncode:
            raise ManagerError(f"Cannot start proxy: {result.stderr.strip()}")
        deadline = time.monotonic() + 30
        while not memory.port_open(config.PROXY_PORT) and time.monotonic() < deadline:
            time.sleep(0.3)
        if not memory.port_open(config.PROXY_PORT):
            raise ManagerError("Proxy did not become ready within 30 seconds")
