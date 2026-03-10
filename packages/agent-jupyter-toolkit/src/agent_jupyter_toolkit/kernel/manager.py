"""
Kernel lifecycle management.
Manages kernel start, stop, restart, health checks, and exposes channels.
"""

import asyncio
import contextlib
import logging
import os

from jupyter_client.asynchronous.client import AsyncKernelClient
from jupyter_client.manager import AsyncKernelManager
from jupyter_core.paths import jupyter_runtime_dir

from .types import KernelError

logger = logging.getLogger(__name__)


class KernelManager:
    """
    Manages a single Jupyter kernel using AsyncKernelManager and AsyncKernelClient.
    """

    def __init__(
        self,
        kernel_name: str = "python3",
        startup_timeout: float = 60.0,
        connection_file_name: str | None = None,
        packer: str | None = None,
    ):
        self.kernel_name = kernel_name
        self.startup_timeout = startup_timeout
        self._km: AsyncKernelManager | None = None
        self._kc: AsyncKernelClient | None = None
        self._lock = asyncio.Lock()
        self._connection_file_name = connection_file_name
        self._connection_file_path: str | None = None
        self._packer = packer

    async def start(self):
        async with self._lock:
            if self._km is not None:
                logger.warning("Kernel already started.")
                return
            self._km = AsyncKernelManager(kernel_name=self.kernel_name)

            # Allow caller to pick a predictable connection file name
            if self._connection_file_name:
                cf = self._connection_file_name
                if not os.path.isabs(cf):
                    cf = os.path.join(jupyter_runtime_dir(), cf)
                self._km.connection_file = cf

            await self._km.start_kernel()
            self._kc = self._km.client()
            if self._kc and self._packer:
                self._kc.session.packer = self._packer
            self._kc.start_channels()
            await self._kc.wait_for_ready(timeout=self.startup_timeout)

            # Resolve absolute connection_file path for later use/logging
            cf = self._km.connection_file
            if not os.path.isabs(cf):
                cf = os.path.join(self._km.runtime_dir, cf)
            self._connection_file_path = cf

            logger.info(
                "Kernel started, channels opened, ready. connection_file=%s",
                self._connection_file_path,
            )

    async def connect_to_existing(self, connection_file: str):
        """
        Attach to an already-running kernel given its connection file (JSON with ZMQ ports + key).
        """
        async with self._lock:
            if self._kc or self._km:
                raise KernelError("Kernel already started or connected.")
            self._kc = AsyncKernelClient()
            self._kc.load_connection_file(connection_file)
            if self._packer:
                self._kc.session.packer = self._packer
            self._kc.start_channels()
            await self._kc.wait_for_ready(timeout=self.startup_timeout)

            self._connection_file_path = os.path.abspath(connection_file)
            logger.info("Connected to existing kernel via %s", self._connection_file_path)

    async def shutdown(self):
        async with self._lock:
            if self._kc:
                try:
                    self._kc.stop_channels()
                except Exception as e:
                    logger.warning(f"Error stopping kernel channels: {e}")
            if self._km:
                try:
                    await self._km.shutdown_kernel(now=True)
                except Exception as e:
                    logger.warning(f"Error shutting down kernel: {e}")
            self._kc = None
            self._km = None
            self._connection_file_path = None
            logger.info("Kernel shutdown complete.")

    async def restart(self):
        async with self._lock:
            if not self._km:
                raise KernelError("No kernel to restart.")
            old_client = self._kc
            if old_client:
                with contextlib.suppress(Exception):
                    old_client.stop_channels()
            await self._km.restart_kernel(now=True)
            self._kc = self._km.client()
            if self._kc and self._packer:
                self._kc.session.packer = self._packer
            self._kc.start_channels()
            await self._kc.wait_for_ready(timeout=self.startup_timeout)
            logger.info("Kernel restarted and ready.")

    async def interrupt(self) -> None:
        """
        Send an interrupt (SIGINT) to the kernel process.

        Requires a managed kernel (started via ``start()``).  Kernels
        attached via ``connect_to_existing()`` do not expose a process
        handle, so an interrupt is not possible in that scenario.

        Raises:
            KernelError: If no managed kernel is available.
        """
        async with self._lock:
            if not self._km:
                raise KernelError("No managed kernel to interrupt.")
            await self._km.interrupt_kernel()
            logger.info("Kernel interrupted.")

    async def is_alive(self) -> bool:
        if self._km is None:
            return False
        try:
            return await self._km.is_alive()
        except Exception:
            return False

    async def is_healthy(self) -> bool:
        if self._kc is None or self._km is None:
            return False
        try:
            reply = await self._kc.kernel_info(reply=True, timeout=5)
            return reply.get("content", {}).get("status") == "ok"
        except Exception as e:
            logger.warning(f"Kernel health check failed: {e}")
            return False

    @property
    def client(self) -> AsyncKernelClient | None:
        return self._kc

    @property
    def connection_file_path(self) -> str | None:  # NEW
        return self._connection_file_path

    @property
    def shell_channel(self):
        return self._kc.shell_channel if self._kc else None

    @property
    def iopub_channel(self):
        return self._kc.iopub_channel if self._kc else None

    @property
    def stdin_channel(self):
        return self._kc.stdin_channel if self._kc else None

    @property
    def control_channel(self):
        return self._kc.control_channel if self._kc else None

    @property
    def hb_channel(self):
        return self._kc.hb_channel if self._kc else None
