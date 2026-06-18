# LingTai SDK — runnable examples

Small, **offline, secret-free** scripts that exercise the public
`lingtai_sdk` surface. Each is self-contained and runnable directly:

```bash
python docs/sdk/examples/01_query_offline.py
```

| File | Shows | Network? |
|------|-------|----------|
| [`01_query_offline.py`](01_query_offline.py) | `query` / `LingTaiClient.query` one-shot against an injected fake runtime | no |
| [`02_session_offline.py`](02_session_offline.py) | `LingTaiClient.open_session` / `LingTaiSession` multi-message + incremental `events()` cursor | no |
| [`03_native_runtime.py`](03_native_runtime.py) | Configuring `NativeRuntime` + the lazy-wrapper boundary (stops before `start()`) | no |
| [`04_registry_and_guard.py`](04_registry_and_guard.py) | `default_registry` dispatch-target lookup + the `guard` advisory/deny bridge | no |

Why they inject a fake runtime: the default backend (`NativeRuntime`) boots a
real wrapper `Agent` and needs an LLM provider + API key. To stay offline and
deterministic — and to keep these examples runnable in CI without credentials —
the `query`/session examples inject a tiny echo runtime that implements the
`lingtai_sdk.runtime.Runtime` contract. This is exactly how the SDK's own test
suite drives the facade. Example `03` shows the *real* `NativeRuntime` but stops
short of `start()`, so no agent boots and no key is needed.

These files are syntax- and import-checked by
`tests/test_sdk_docs_examples.py`.

See the [SDK guide](../README.md) for the narrative walk-through.
