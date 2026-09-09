from app.core.metrics import output_size_bytes, render_duration_seconds, render_total


def test_render_metrics_contain_no_caller_controlled_dimensions():
    assert render_duration_seconds._labelnames == ("output_format",)
    assert render_total._labelnames == ("output_format", "status")
    assert output_size_bytes._labelnames == ("output_format",)
