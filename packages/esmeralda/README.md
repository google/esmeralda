# esmeralda

The operational runtime shared by every Esmeralda agent on Agent Runtime:
- the `esmeralda run` container entrypoint, which installs the Agent Gateway root CA;
- `prepare()` / `finalize()`, the process lifecycle;
- `EsmeraldaTelemetryPlugin`, for caller context and telemetry in every serving mode;
- `create_app()`.

Documentation (also published on the docs site under **Esmeralda Library**):

- [Overview](../../docs/src/4-esmeralda-library/index.md): what it does and why.
- [Usage guide](../../docs/src/4-esmeralda-library/guide.md): how to use it, case by case.
- [Package reference](../../docs/src/4-esmeralda-library/reference.md): modules, functions, environment variables and event schemas.

Minimal agent package (`agent/__init__.py`):

```python
import esmeralda

esmeralda.prepare()

from .agent import root_agent  # noqa: E402

app = esmeralda.create_app(root_agent)
```

Tests:

```bash
uv run --frozen --package esmeralda --extra dev pytest packages/esmeralda/tests
```
