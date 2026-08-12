# Adapter module
from .adapter import Adapter
from .contract import (
    StructuredMetrics, SimError,
    SnapshotPackage, SnapshotHeader, Capability,
    ObservationContext, DigitalTwin, DesignObject,
    ArtifactInfo, ExecutionTraceEntry,
)
from .runner import (Backend, BackendRegistry, create_backend,
                     BackendError, BackendNotFoundError, BackendExecutionError)

__all__ = [
    "Adapter",
    "StructuredMetrics", "SimError",
    "SnapshotPackage", "SnapshotHeader", "Capability",
    "ObservationContext", "DigitalTwin", "DesignObject",
    "ArtifactInfo", "ExecutionTraceEntry",
    "Backend", "BackendRegistry", "create_backend",
    "BackendError", "BackendNotFoundError", "BackendExecutionError",
]
