---
timeout: 180
---

# Test: Shallow vs Deep

## Setup

1. A running parent agent with non-empty `system/` and `codex/` directories.
2. Network root directory with write permissions.

## Steps

### Scenario A: Shallow avatar

1. **Spawn shallow avatar**.
   ```python
   avatar(action="spawn", name="shallow-test", type="shallow", reasoning="shallow test")
   ```

2. **Verify shallow workdir** — exists; `system/`, `codex/`, `exports/` do NOT exist.
   ```bash
   test -d <root>/shallow-test && \
   ! test -d <root>/shallow-test/system && \
   ! test -d <root>/shallow-test/codex && \
   ! test -d <root>/shallow-test/exports && \
   echo "PASS" || echo "FAIL"
   ```

3. **Verify init.json** — `agent_name`, `admin={}`, `prompt=""`.
   ```bash
   python3 -c "
   import json
   d = json.load(open('<root>/shallow-test/init.json'))
   assert d['manifest']['agent_name'] == 'shallow-test'
   assert d['manifest']['admin'] == {}
   assert d['prompt'] == ''
   print('PASS')
   "
   ```

4. **Verify heartbeat** — appears within 5s (avatar booted successfully).
   ```bash
   for i in $(seq 1 50); do
     test -f <root>/shallow-test/.agent.heartbeat && echo "PASS" && break
     sleep 0.1
   done
   ```

### Scenario B: Deep avatar

5. **Spawn deep avatar**.
   ```python
   avatar(action="spawn", name="deep-test", type="deep", reasoning="deep test")
   ```

6. **Verify deep copies exist** — `system/` and `codex/` match parent.
   ```bash
   diff -rq <parent>/system <root>/deep-test/system && \
   diff -rq <parent>/codex <root>/deep-test/codex && \
   echo "PASS" || echo "FAIL"
   ```

7. **Verify runtime state NOT copied** — `history/`, `mailbox/`, `delegates/` absent.
   ```bash
   ! test -d <root>/deep-test/history && \
   ! test -d <root>/deep-test/mailbox && \
   ! test -d <root>/deep-test/delegates && \
   echo "PASS" || echo "FAIL"
   ```

8. **Verify init.json** — same modifications as shallow (name, admin, prompt).
   ```bash
   python3 -c "
   import json
   d = json.load(open('<root>/deep-test/init.json'))
   assert d['manifest']['agent_name'] == 'deep-test'
   assert d['manifest']['admin'] == {}
   assert d['prompt'] == ''
   print('PASS')
   "
   ```

9. **Verify heartbeat** — appears within 5s.
   ```bash
   for i in $(seq 1 50); do
     test -f <root>/deep-test/.agent.heartbeat && echo "PASS" && break
     sleep 0.1
   done
   ```

10. **Verify scope guard** — `_prepare_deep` refuses non-sibling destinations.
    ```bash
    python3 -c "
    from pathlib import Path
    from lingtai.core.avatar import AvatarManager
    try:
        AvatarManager._prepare_deep(Path('/a/b'), Path('/c/d'))
        print('FAIL: should have raised')
    except ValueError as e:
        assert 'not a sibling' in str(e)
        print('PASS')
    "
    ```

## Pass criteria

- Shallow: workdir with only `init.json`, `.prompt`, `logs/`. No `system/`, `codex/`, `exports/`.
- Deep: `system/` and `codex/` match parent. No `history/`, `mailbox/`, `delegates/`.
- Both: identical `init.json` transformations. Both produce heartbeat within 5s.
- Scope guard: `_prepare_deep` raises ValueError for non-sibling paths.

## Output template

```
## Shallow vs Deep Test
| Scenario | Step | Check | Result |
|----------|------|-------|--------|
| A | 1 | Shallow spawn | |
| A | 2 | Shallow contents | |
| A | 3 | init.json | |
| A | 4 | Heartbeat | |
| B | 5 | Deep spawn | |
| B | 6 | Deep copies | |
| B | 7 | No runtime state | |
| B | 8 | init.json | |
| B | 9 | Heartbeat | |
| — | 10 | Scope guard | |
```
