"""Background polling and cached reads for future HTTP printer adapters."""

import asyncio
import logging
import math
import time
from abc import ABC, abstractmethod
from dataclasses import replace

from backend.app.services.printer_adapter import (
    FleetPrinterStatus,
    PrinterActivity,
    PrinterCapabilities,
    PrinterFamily,
    PrinterReadiness,
)

logger = logging.getLogger(__name__)


def offline_snapshot(status: FleetPrinterStatus) -> FleetPrinterStatus:
    """Preserve diagnostic telemetry without advertising it as current/ready."""
    return replace(
        status,
        connected=False,
        activity=PrinterActivity.OFFLINE,
        readiness=PrinterReadiness.OFFLINE,
    )


class PollingPrinterAdapter(ABC):
    """One serial polling task per transport, independent of dashboard reads.

    Subclasses must use truly asynchronous I/O in fetch_status, with their own
    HTTP timeouts. Cancellation must propagate so stop and request deadlines
    can finish. Discovery belongs in transport setup, not in each status read.
    Synchronous network libraries must not run directly in fetch_status.
    """

    def __init__(
        self,
        *,
        poll_interval: float = 5,
        request_timeout: float = 10,
        stale_after: float = 30,
        max_backoff: float = 60,
    ):
        values = (poll_interval, request_timeout, stale_after, max_backoff)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("Polling intervals and timeouts must be finite and positive")
        if max_backoff < poll_interval:
            raise ValueError("Maximum backoff must be at least the polling interval")
        self._poll_interval = poll_interval
        self._request_timeout = request_timeout
        self._stale_after = stale_after
        self._max_backoff = max_backoff
        self._task: asyncio.Task | None = None
        self._last_success_at: float | None = None
        self._snapshot = FleetPrinterStatus(
            connected=False,
            activity=PrinterActivity.OFFLINE,
            readiness=PrinterReadiness.OFFLINE,
            native_state="unknown",
        )

    @property
    @abstractmethod
    def family(self) -> PrinterFamily: ...

    @property
    @abstractmethod
    def capabilities(self) -> PrinterCapabilities: ...

    @abstractmethod
    async def fetch_status(self) -> FleetPrinterStatus:
        """Fetch and normalize one sample; only the background task calls this."""
        ...

    @abstractmethod
    async def pause(self) -> bool: ...

    @abstractmethod
    async def resume(self) -> bool: ...

    @abstractmethod
    async def cancel(self) -> bool: ...

    def get_status(self) -> FleetPrinterStatus:
        """Return cached data; elapsed freshness checks perform no I/O."""
        if self._last_success_at is None or time.monotonic() - self._last_success_at >= self._stale_after:
            return offline_snapshot(self._snapshot)
        return self._snapshot

    async def start(self) -> None:
        """Return immediately; repeated starts never create competing polls."""
        if self._task is not None and not self._task.done():
            return
        self._last_success_at = None
        self._snapshot = offline_snapshot(self._snapshot)
        self._task = asyncio.create_task(self._run(), name=f"printer-status-{self.family.value}")

    async def stop(self) -> None:
        """Await cancellation before an owner replaces this transport."""
        task = self._task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if self._task is task:
                self._task = None
        self._snapshot = offline_snapshot(self._snapshot)

    async def _run(self) -> None:
        retry_delay = self._poll_interval
        try:
            while True:
                try:
                    status = await asyncio.wait_for(self.fetch_status(), timeout=self._request_timeout)
                    self._snapshot = status
                    self._last_success_at = time.monotonic()
                    retry_delay = self._poll_interval
                    delay = self._poll_interval
                except Exception as exc:
                    self._snapshot = offline_snapshot(self._snapshot)
                    # Exception text can contain credentials or server URLs.
                    logger.debug("Printer status refresh failed (%s)", type(exc).__name__)
                    delay = retry_delay
                    retry_delay = min(retry_delay * 2, self._max_backoff)
                await asyncio.sleep(delay)
        finally:
            self._snapshot = offline_snapshot(self._snapshot)
