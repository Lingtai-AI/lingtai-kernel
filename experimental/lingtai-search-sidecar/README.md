# lingtai-search-sidecar PoC

Experimental Rust sidecar for LingTai file search. It is not part of the
package build and is not used by default. Build manually with:

```bash
cd experimental/lingtai-search-sidecar
cargo build
```

The binary accepts one JSON request on stdin and writes one JSON response to
stdout. The current PoC only implements a literal substring `grep` operation;
its purpose is to prove the runtime boundary, not to replace the Python backend.
