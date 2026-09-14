"""Prometheus metrics for the render path."""

from prometheus_client import Counter, Gauge, Histogram

# Render operation metrics
render_duration_seconds = Histogram(
    "prelum_render_duration_seconds",
    "Time spent rendering templates",
    ["output_format"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0),
)

render_total = Counter(
    "prelum_render_total",
    "Total number of render requests",
    ["output_format", "status"],
)

# Concurrent render tracking
concurrent_renders = Gauge(
    "prelum_concurrent_renders",
    "Number of currently active render operations",
)

render_queue_waiting = Gauge(
    "prelum_render_queue_waiting",
    "Number of render requests waiting for semaphore",
)

# Output metrics
output_size_bytes = Histogram(
    "prelum_output_size_bytes",
    "Size of rendered output in bytes",
    ["output_format"],
    buckets=(1024, 10240, 102400, 512000, 1048576, 5242880, 10485760, 52428800),
)

# Error tracking
render_errors_total = Counter(
    "prelum_render_errors_total",
    "Total number of render errors by type",
    ["error_type"],
)

render_timeouts_total = Counter(
    "prelum_render_timeouts_total",
    "Total number of render timeouts",
)
