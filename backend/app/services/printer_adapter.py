"""Vendor-neutral contracts for the fleet manager.

This interface is additive: legacy Bambu callers still use PrinterState.
Adapters must return detached snapshots, use seconds for time and percent
(0..100) for progress, and never infer readiness from connection alone.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class PrinterFamily(str, Enum):
    BAMBU = "bambu"
    KLIPPER = "klipper"
    DUET = "duet"


class PrinterActivity(str, Enum):
    UNKNOWN = "unknown"
    OFFLINE = "offline"
    IDLE = "idle"
    PREPARING = "preparing"
    PRINTING = "printing"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"


class PrinterReadiness(str, Enum):
    UNKNOWN = "unknown"
    OFFLINE = "offline"
    READY = "ready"
    BUSY = "busy"
    BLOCKED = "blocked"
    AWAITING_CLEARANCE = "awaiting_clearance"


@dataclass(frozen=True)
class TemperatureReading:
    """Celsius; None means unavailable, while zero is a valid reading."""

    identifier: str
    current: float | None = None
    target: float | None = None


@dataclass(frozen=True)
class PrinterCapabilities:
    """Operations implemented by this adapter, not every vendor feature."""

    pause: bool = False
    resume: bool = False
    cancel: bool = False


@dataclass(frozen=True)
class FleetPrinterStatus:
    connected: bool
    activity: PrinterActivity
    readiness: PrinterReadiness
    native_state: str
    filename: str | None = None
    progress_percent: float | None = None
    remaining_seconds: int | None = None
    # UTC Unix timestamp of last inbound communication; not snapshot creation.
    last_seen_at: float | None = None
    tools: tuple[TemperatureReading, ...] = ()
    bed: TemperatureReading | None = None
    chamber: TemperatureReading | None = None
    error_codes: tuple[str, ...] = ()


@runtime_checkable
class PrinterAdapter(Protocol):
    """Common status and controls; transport lifecycle stays with its owner.

    Controls report command submission, not confirmed physical completion.
    Future network adapters can await their HTTP operations directly.
    Upload/start and transport event subscriptions will extend this contract
    when the first non-Bambu transport is implemented.
    """

    @property
    def family(self) -> PrinterFamily: ...

    @property
    def capabilities(self) -> PrinterCapabilities: ...

    def get_status(self) -> FleetPrinterStatus:
        """Read local telemetry only; never perform I/O or trigger reconnects."""
        ...

    async def pause(self) -> bool: ...

    async def resume(self) -> bool: ...

    async def cancel(self) -> bool: ...


class ManagedPrinterAdapter(PrinterAdapter, Protocol):
    """Transport owned by the fleet manager, rather than legacy Bambu code."""

    async def start(self) -> None:
        """Start background work without waiting for a network connection."""
        ...

    async def stop(self) -> None:
        """Cancel and await background work, then release transport resources."""
        ...
