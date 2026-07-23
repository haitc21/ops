from ops.observability.metrics import MetricsRegistry


def test_metrics_render_only_counter_names_and_values() -> None:
    registry = MetricsRegistry()
    registry.increment("ops_commands_retried_total", 2)
    assert registry.render_prometheus() == "ops_commands_retried_total 2\n"
