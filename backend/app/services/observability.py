from app.config import get_settings


def configure_observability(app=None):
    if not get_settings().otel_enabled:
        return None
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": "researchforge-api" if app else "researchforge-worker"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=get_settings().otel_endpoint)))
    trace.set_tracer_provider(provider)
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls="health,telemetry/metrics")
    return provider
