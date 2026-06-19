# LingTai SDK — developer guide

> A practical, human-facing tour of the public `lingtai_sdk` surface: what it is
> today, what it deliberately is **not** yet, the canonical import paths, and
> runnable quick examples. For the staged engineering log — every PR, every
> deferred slice, the rationale behind each contract — see
> [`architecture-foundation.md`](architecture-foundation.md). This page is the
> doorway; that file is the blueprint.

## What the LingTai SDK is

`lingtai_sdk` is the **curated public front door** for building and embedding
LingTai agents in-process. It is a thin consumer of the two implementation
packages — never a dependency of them:

```
lingtai_sdk  ──imports──▶  lingtai.kernel   (eager; zero hard deps)
            ──imports──▶  lingtai          (lazy; the batteries-included wrapper)
```

What it gives you today:

- **Programmable contracts** — provider-agnostic dataclasses/ABCs describing how
  a runtime is driven (`RuntimeOptions` in, `RuntimeMessage` in, a stream of
  `RuntimeEvent` out) and how a capability is declared (`BundleManifest`). These
  are the stable shapes embedders build against.
- **A native runtime adapter** — `NativeRuntime` wraps the existing wrapper
  `Agent` *unchanged* and drives it through the runtime contract. It is the
  default backend behind the client facade.
- **A thin client/session facade** — `query`, `LingTaiClient`, `open_session`,
  `LingTaiSession`: ergonomic one-shot and multi-message entry points over any
  runtime.
- **Capability / bundle declarations** — every SDK capability is declared as a
  `BundleManifest`; `default_registry()` gives a validated, indexed view of the
  whole declared set, and `dispatch_target()` resolves a tool name to its owning
  bundle and declared danger posture.
- **A guard bridge** — `lingtai_sdk.guard` turns those declared danger postures
  into a kernel `GuardCheck` (allow / advisory-warn / deny), so a host can
  consult declared posture *before* a tool is dispatched.

`import lingtai_sdk` stays as cheap and side-effect-free as
`import lingtai.kernel`: only the kernel is loaded eagerly. The wrapper and its
heavy provider SDKs (anthropic, openai, google-genai, mcp, …) load **lazily** —
the first time you touch a wrapper-backed name like `Agent`, or the first time a
`NativeRuntime` session is actually started.

## What the SDK is NOT yet

This is a **foundation surface**: stable contracts and a doorway, with the
native runtime and bundle declarations wired in. Several things are deliberately
*deferred* — naming them here so you don't build against a promise that isn't
there:

- **CLI product assembly.** The CLI stays exactly where it is: `lingtai.cli`
  (`lingtai-agent run …`). The SDK is the *importable* surface; it does not
  assemble or replace the CLI.
- **A non-native backend.** Only `NativeRuntime` (wrapping `Agent`) exists. An
  Anthropic — or any other non-native — backend that maps `RuntimeOptions` onto
  a provider client is a later stage.
- **Dispatch *through* a bundle host.** The registry and guard are **read-only
  metadata**: they answer "which bundle owns this tool" and "how dangerous is
  it," not "call the tool." No live agent dispatches through a bundle host yet;
  real handler wiring and guard installation remain deferred, higher-risk slices.
- **A package split / distribution rename.** `lingtai_sdk` ships *inside* the
  existing `lingtai` wheel. Making it the headline published package is a later
  step.
- **Any change to existing `lingtai` / `lingtai.kernel` runtime behavior.** This
  whole surface is additive; it changes no kernel turn-loop behavior.

## Canonical import paths (after #368) and the legacy-shim policy

The SDK package is organized into four subpackages — `runtime/`, `client/`,
`guard/`, `bundles/` — with the curated names also re-exported from the package
root. Prefer the **package root** for the common surface and the **subpackage**
for contract types:

```python
# The common surface — import straight from the package root:
from lingtai_sdk import (
    BaseAgent, Agent,                       # agents (kernel eager, wrapper lazy)
    query, LingTaiClient, LingTaiSession, open_session, QueryResult,
    NativeRuntime, NativeRuntimeSession,    # default backend (lazy)
    default_registry, all_bundle_manifests, BundleRegistry, DispatchTarget,
)

# Contract types live in their subpackage:
from lingtai_sdk.runtime import (
    Runtime, RuntimeSession, RuntimeOptions, RuntimeMessage, RuntimeEvent,
    RuntimeState, EventKind,
)
from lingtai_sdk.bundles.contracts import BundleManifest, SecurityDanger
from lingtai_sdk.guard import GuardPolicyMode, guard_check_from_manifests
```

**Legacy-shim policy.** Every pre-#368 flat module path still resolves — to the
*same object*, never a fork or re-implementation:

| Canonical (preferred) | Legacy shim (still works) |
|-----------------------|---------------------------|
| `lingtai_sdk.runtime` (package) | `import lingtai_sdk.runtime` (flat module) — same path, now a package |
| `lingtai_sdk.client` (package) | `from lingtai_sdk.client import query` — same path, now a package |
| `lingtai_sdk.guard.bridge` | `lingtai_sdk.guard_bridge` |
| `lingtai_sdk.bundles.contracts` | `lingtai_sdk.capabilities` |
| `lingtai_sdk.bundles.registry` | `lingtai_sdk.bundle_registry` |
| `lingtai_sdk.bundles.native` | `lingtai_sdk.native` |
| `lingtai_sdk.bundles.<x>_tools` | `lingtai_sdk.<x>_tools` |

`runtime` and `client` are packages whose `__init__.py` *is* the compatibility
surface (a directory and a same-named `.py` cannot coexist, so the package
wins). Every other moved module keeps a real `.py` shim at its old top-level
path that re-exports from the new location. The same-object guarantee is pinned
by `tests/test_sdk_directory_shape.py`.

There is a *second*, older compatibility map for names that moved out of the
implementation packages into the SDK (`lingtai.kernel.BaseAgent` →
`lingtai_sdk.BaseAgent`, `lingtai.Agent` → `lingtai_sdk.Agent`, …). It lives in
`lingtai_sdk._compat.DEPRECATIONS` and is enforced by `tests/test_sdk_compat.py`.
No name is removed within a major version; a legacy path graduates from "active
alias" to "removed" only across a major bump.

### Choosing canonical vs legacy

- **New code** → import canonical paths (package root for the common surface,
  subpackage for contract types). They are the stable, documented home.
- **Existing code** → no rush. Legacy paths resolve to the same objects and are
  not removed within a major version. Migrate opportunistically when you touch
  the file; there is no behavioral difference, only a clearer import.
- **Tooling / type-only environments** → import `lingtai_sdk`, `lingtai_sdk.runtime`,
  `lingtai_sdk.bundles.*`, or `lingtai_sdk.guard` freely: they are import-pure
  (kernel-only) and pull in no provider SDK. Touch `Agent` / the service classes
  / start a `NativeRuntime` session only where the wrapper is actually available.

## Quick examples

Every snippet below is a real, **offline** example committed under
[`examples/`](examples/) and syntax/import-checked by
`tests/test_sdk_docs_examples.py`. Run any of them directly, e.g.
`python docs/sdk/examples/01_query_offline.py`.

### `query` — one message, one result

`query` (and `LingTaiClient.query`) is runtime-agnostic: it drives any object
implementing the `Runtime` contract. The default is `NativeRuntime`, which boots
a real `Agent` and needs an LLM key — so to stay offline these examples inject a
tiny echo runtime (see [`01_query_offline.py`](examples/01_query_offline.py) for
the full `EchoRuntime`):

```python
from lingtai_sdk import LingTaiClient, query
from lingtai_sdk.runtime import RuntimeOptions

options = RuntimeOptions(working_dir="/tmp/lingtai-sdk-example")

client = LingTaiClient(runtime=EchoRuntime())          # inject a fake backend
result = client.query("hello", options=options)
print(result.text)                                      # -> "echo: hello"
print([e.kind.value for e in result.events])            # -> ['state', 'text', 'state']

# Module-level one-shot, no client to hold:
query("hi", options=options, runtime=EchoRuntime())
```

With no injected runtime (`LingTaiClient()` / `query(..., runtime=None)`), the
default `NativeRuntime` is used and a real agent boots — that path needs a
provider, model, and key.

### `LingTaiClient` / session — keep it open

`open_session` returns a `LingTaiSession` for multi-message use. It keeps its
own **read cursor**, so each `events()`/`text()` returns only what arrived since
the last read (see [`02_session_offline.py`](examples/02_session_offline.py)):

```python
client = LingTaiClient(runtime=EchoRuntime(), options=options)

with client.open_session() as session:        # starts; closes on exit
    session.send("first")
    print(session.text())                       # -> "echo: first"  (incremental)
    session.send("second")
    print(session.text())                       # -> "echo: second"
    session.raw_session.events()                # full cumulative snapshot
```

### `NativeRuntime` — the default backend

`NativeRuntime` wraps the wrapper `Agent` unchanged. Constructing it and a
session is import-pure: the wrapper and provider SDKs load lazily only at
`start()`. [`03_native_runtime.py`](examples/03_native_runtime.py) configures a
session and stops before `start()`, so no key is needed:

```python
from lingtai_sdk import NativeRuntime
from lingtai_sdk.runtime import RuntimeOptions, RuntimeState

runtime = NativeRuntime()
options = RuntimeOptions(
    working_dir="/tmp/lingtai-sdk-native",
    agent_name="demo",
    provider="anthropic",
    model="claude-opus-4-8",
    capabilities=["file", "web_search"],
    # api_key=...  # only needed once you call start()
)
session = runtime.create_session(options)
assert session.state is RuntimeState.PENDING    # no Agent built yet
# session.start()  # <- imports the wrapper + provider SDK and boots a real agent
```

### `default_registry` — declared bundles, indexed

`default_registry()` is a validated view over the full declared bundle set.
`dispatch_target` resolves a tool to its owning bundle and declared posture
(read-only metadata; see
[`04_registry_and_guard.py`](examples/04_registry_and_guard.py)):

```python
from lingtai_sdk import default_registry

registry = default_registry()
registry.names()
# -> ('system', 'psyche', 'soul', 'read', 'glob', 'grep', 'write', 'edit',
#     'email', 'daemon', 'mcp', 'knowledge', 'skills', 'bash',
#     'avatar_spawn', 'avatar_rules')

target = registry.dispatch_target("bash")
print(target.bundle_name, target.danger.value)   # -> "bash destructive"
```

### Guard / advisory metadata — declared posture → decision

`lingtai_sdk.guard` turns declared bundle postures into a kernel `GuardCheck`.
The policy is narrow and fail-open: `safe` → clean pass-through, `caution` →
allow-but-warn, `destructive` → deny (BLOCKING) or warn (ADVISORY), unknown
tool → allow:

```python
from lingtai_sdk import all_bundle_manifests
from lingtai_sdk.guard import GuardPolicyMode, guard_check_from_manifests
from lingtai.kernel.tool_call_guard import ToolProposal

check = guard_check_from_manifests(all_bundle_manifests(), mode=GuardPolicyMode.BLOCKING)
check(ToolProposal(tool_name="read", tool_args={}))   # -> None  (clean allow)
check(ToolProposal(tool_name="edit", tool_args={}))   # -> allow + "warn" advisory
check(ToolProposal(tool_name="bash", tool_args={}))   # -> deny  (BLOCKING)
```

The returned check is pure and stateless over a frozen snapshot of the
manifests. Building it wires nothing into a live agent — installing it into a
real `ToolExecutor` guard seam is a deferred slice.

## Where to go next

- [`architecture-foundation.md`](architecture-foundation.md) — the full staged
  roadmap and the rationale behind every contract.
- [`examples/`](examples/) — the runnable scripts quoted above.
- `src/lingtai_sdk/ANATOMY.md` — the per-folder structural map of the package.
