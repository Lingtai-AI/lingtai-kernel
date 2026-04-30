# Test Result: Bash Capability (shell)

> **Tester:** test-bash (agent_id: 20260430-082407-5336)  
> **Date:** 2026-04-30  
> **Yolo mode:** enabled (`yolo=True`)  
> **Agent working dir:** `/Users/huangzesen/work/lingtai-projects/lingtai-dev/.lingtai/test-bash`  
> **Contract under test:** `leaves/capabilities/shell/bash/README.md`, `yolo/README.md`, `sandbox/README.md`, `kill/README.md`

---

## 1. Bash — Normal (正路)

### 1.1 Basic stdout

```
Command:  echo hello
Expect:   stdout='hello\n', exit_code=0
Actual:   status='ok', exit_code=0, stdout='hello\n', stderr=''
Verdict:  ✅ PASS — conforms to contract
```

### 1.2 Non-zero exit

```
Command:  exit 1
Expect:   exit_code=1
Actual:   status='ok', exit_code=1, stdout='', stderr=''
Verdict:  ✅ PASS
```

### 1.3 Stderr / stdout separation

```
Command:  echo "stderr test" >&2 && echo "stdout test"
Expect:   stdout='stdout test\n', stderr='stderr test\n'
Actual:   status='ok', exit_code=0, stdout='stdout test\n', stderr='stderr test\n'
Verdict:  ✅ PASS — streams are correctly separated
```

### 1.4 Pipe + chain

```
Command:  echo "line1" && echo "line2" | cat
Actual:   stdout='line1\nline2\n', exit_code=0
Verdict:  ✅ PASS — pipe parsing works
```

### 1.5 Subshell substitution

```
Command:  echo "nested: $(echo inner)"
Actual:   stdout='nested: inner\n', exit_code=0
Verdict:  ✅ PASS
```

### 1.6 Env-var prefix

```
Command:  MYVAR=hello && echo $MYVAR
Actual:   stdout='hello\n', exit_code=0
Verdict:  ✅ PASS — env-var prefix extraction (not a command) works
```

### 1.7 Empty command

```
Command:  (empty string)
Expect:   status='error', message contains 'required'
Actual:   status='error', message='command is required'
Verdict:  ✅ PASS
```

---

## 2. Bash — Timeout & Kill (超时与终止)

### 2.1 Basic timeout

```
Command:  sleep 5
Timeout:  2s
Expect:   status='error', message='Command timed out after 2s'
Actual:   status='error', message='Command timed out after 2s', _elapsed_ms=2004
Verdict:  ✅ PASS — timeout fires correctly at ~2s
```

### 2.2 Short timeout (1s)

```
Command:  sleep 60
Timeout:  1s
Expect:   status='error', message='Command timed out after 1s'
Actual:   status='error', message='Command timed out after 1s', _elapsed_ms=1004
Verdict:  ✅ PASS
```

### 2.3 Grandchild leak (孙进程泄漏)

```
Command:  bash -c 'sleep 300 & echo "child_pid=$!"'
Timeout:  2s
Expect:   timeout kills shell; grandchild sleep 300 survives (per kill/README)
Actual:   
  - timeout returned error as expected
  - `ps aux | grep 'sleep 300'` found PID 29131 STILL RUNNING after timeout
  - confirmed leak; manually killed with `kill 29131`

Verdict:  ✅ CONFIRMED — contract is accurate: no process-group kill, grandchild leaks
Note:     This is a real operational risk. Any command with backgrounded children
          will leave orphans on timeout. The README correctly documents this as "NOT implemented."
```

---

## 3. Sandbox — Working Directory Containment (沙箱)

### 3.1 Blocked: /tmp (outside agent dir)

```
Command:     echo hello
Working_dir: /tmp
Expect:      status='error', message contains 'must be under agent working directory'
Actual:      status='error', message='working_dir must be under agent working directory: /Users/huangzesen/work/lingtai-projects/lingtai-dev/.lingtai/test-bash'
Verdict:     ✅ PASS
```

### 3.2 Allowed: absolute path to agent subdir

```
Command:     echo "from subdir" && pwd
Working_dir: /Users/huangzesen/work/.../test-bash/logs  (absolute)
Expect:      status='ok'
Actual:      status='ok', stdout='from subdir\n/Users/huangzesen/work/.../test-bash/logs\n'
Verdict:     ✅ PASS
```

### 3.3 ⚠️ BUG — Relative path `./logs` rejected (相对路径被拒)

```
Command:     echo hello
Working_dir: ./logs
Expect:      status='ok' (./logs resolves to agent subdir)
Actual:      status='error', same 'must be under' message as /tmp

Root cause:  Path("./logs").resolve() is called WITHOUT cwd=agent_dir.
             Python's Path.resolve() uses the PROCESS working directory, 
             which is the Python interpreter's cwd, NOT the agent's working dir.
             So ./logs resolves to the Python process cwd (likely project root or /),
             which is outside the sandbox.

Impact:      Relative working_dir is silently broken. The README contract 
             implies relative paths should work ("Paths like {agent_dir}/subdir 
             resolve and pass startswith"). The table shows `{agent_dir}/subdir` 
             but the implementation resolves relative to process cwd.

Evidence:    ls -la ./logs from bash (with default cwd) works fine — the dir exists.
             The sandbox validation runs Path(cwd).resolve() without anchoring to agent_dir.

Severity:    Medium — the error message is wrong (claims it's outside sandbox, but it 
             IS inside). A user passing a relative path gets a misleading error.
             Workaround: always use absolute paths.

README says: Sandbox/README.md line 35: "Paths like {agent_dir}/subdir resolve and pass"
Reality:     Only ABSOLUTE paths under agent dir pass. Relative paths resolve against
             Python process cwd, not agent dir.
```

---

## 4. YOLO Mode (无限制模式)

### 4.1 Confirmation: rm allowed under yolo

```
Command:  rm --help  (would be blocked by default policy's deny list for 'rm')
Expect:   status='ok' (yolo = no restrictions)
Actual:   status='ok', exit_code=0, stdout contains rm usage
Verdict:  ✅ PASS — yolo bypasses denylist as documented
```

### 4.2 Tool description check

```
The system prompt tool description for bash has NO policy summary appended.
Per yolo/README.md line 47: "BashPolicy.describe() returns empty string when 
both allow and deny are None. So the tool description has no policy summary appended."
This matches the observed tool description in this session.
Verdict:  ✅ PASS
```

### 4.3 Sandbox still applies under yolo

```
Not separately tested (sandbox tests above all ran under yolo).
All sandbox errors were returned correctly despite yolo=True.
Verdict:  ✅ CONFIRMED — sandbox is orthogonal to policy mode
```

---

## 5. Bash — Edge Cases (边角)

### 5.1 Output truncation

```
Command:  python3 -c "print('A' * 60000)"
Expect:   stdout truncated at ~50,000 chars with suffix per README: 
          "\n... (truncated, {total} chars total)"
Actual:   stdout truncated with suffix: "[truncated — 50035 bytes total]"

⚠️ MISMATCH — README contract vs actual:
  - README says:  "\n... (truncated, {total} chars total)"  (newline prefix, "chars", parenthetical)
  - Actual:       "[truncated — 50035 bytes total]"          (bracket, em-dash, "bytes", no newline)
  - Also: README says "50,000 chars" cap, but actual truncation point is ~50,000 bytes.
    For ASCII text chars==bytes, but for multibyte (UTF-8 CJK etc.) they'd diverge.

Verdict:  ⚠️ PASS (functionally correct — truncation works) 
          but FAIL on contract accuracy (suffix format differs from README)
```

---

## 6. Concurrent Bash Calls (并发调用)

### 6.1 Four parallel bash calls

```
Attempt:   4 bash calls dispatched in the same turn
Result:    ALL 4 failed with kernel health_check:pre_send_pairing
Error msg: "tool execution either timed out, errored, or was interrupted 
           before its result could be committed"
Recovery:  After retrying each call individually (one per turn), all succeeded.

Root cause: Likely a kernel-level issue where parallel bash calls contend 
            for resources or trigger the health check pre-send pairing guard.
            Not a bash capability bug per se — the bash tool itself works fine 
            when called sequentially.

Impact:    Agent must not batch multiple bash calls in one turn. 
            This limits throughput but does not break correctness.

Severity:  Low — workaround is sequential calls. But this is surprising given 
            the daemon tool spawns 4 parallel workers by default.
```

---

## 7. Root Cause Analysis (根因)

**四项发现并非四个独立 bug，乃同一根源之四面：文档与实现之间的渐行渐远。**

| Finding | 文档写于何时之假设 | 实现已演至何处 |
|---------|-------------------|---------------|
| F1 截断后缀格式 | 早期设计稿，`"\n... (truncated)"` | 实现已改用 bracket + em-dash 风格 |
| F2 截断单位 chars→bytes | 文档写 "chars" | 实现用 `len()` on bytes 或 output 字节截断 |
| F3 相对路径 sandbox | 文档假设 resolve 起于 agent dir | 实现用 `Path(cwd).resolve()` 无 anchor |
| F4 并发 bash | 文档未提及并发限制 | kernel health_check 有隐含的顺序约束 |

**共性**：代码在演进中调整了行为，文档未同步更新。这不是"四个 bug"，而是"一处连贯性债务"。修文档或修代码，关键在于先定「契约」——此处的每个行为，设计意图究竟为何？再使文档与实现对齐。

推荐优先级：先查 kernel 源码确认每处是有意变更还是实现偏差，再决定修文档还是修代码。

### 系统性追问

此四瑕若为 bash 独有，属个别失修。但若 kernel 八枚能力（bash、codex、email、avatar、daemon、psyche、soul、web_search）皆有同类文档-实现之隙，则非个别问题，乃**流程缺环**。

根治之策有二，可择一或兼施：
1. **凡改实现者必同步文档**——立规于 PR checklist，代码审时查文档是否同步。
2. **以测试为文档之准**——将本文档之测试用例化为自动化回归测试（如 pytest），测试即活文档，实现与文档不一致时测试自然失败。

此问待父代决：是否值得在其他七枚能力上做同类实测以验证假设？若八枚皆有此隙，则当立流程规，而非逐个修补。

---

## 8. Summary & Findings (原 §7，因根因分析插入而重编号)

### What conforms to contract

| Behavior | README says | Reality | Verdict |
|----------|-------------|---------|---------|
| Basic stdout | `{"stdout": "..."}` | ✅ | PASS |
| Exit codes | `exit_code: <int>` | ✅ | PASS |
| Stderr separation | separate fields | ✅ | PASS |
| Timeout → error | `"Command timed out after {N}s"` | ✅ exact match | PASS |
| Empty command → error | error message | ✅ `"command is required"` | PASS |
| Sandbox blocks outside | error with path | ✅ | PASS |
| Yolo bypasses denylist | `rm` allowed | ✅ | PASS |
| Yolo does NOT bypass sandbox | sandbox still enforced | ✅ | PASS |
| Grandchild leak on timeout | No process-group kill | ✅ confirmed | PASS |

### What deviates from contract (findings)

| # | Finding | README claim | Actual behavior | Severity |
|---|---------|-------------|-----------------|----------|
| F1 | **Truncation suffix format** | `"\n... (truncated, {total} chars total)"` | `"[truncated — 50035 bytes total]"` | Low |
| F2 | **Truncation unit** | "chars" | "bytes" (works for ASCII, wrong for multibyte) | Low-Medium |
| F3 | **Relative working_dir** | Implied supported (`{agent_dir}/subdir`) | Rejected — resolves against process cwd, not agent dir | Medium |
| F4 | **Parallel bash calls** | Not documented | All 4 fail with health_check; must be sequential | Low |

### Recommendations

1. **F3 (sandbox relative path)**: Either fix the implementation to anchor relative paths to agent_dir (`Path(agent_dir) / cwd`), or document that `working_dir` must be absolute.
2. **F1/F2 (truncation)**: Update the README to match actual suffix format. Clarify whether the cap is in chars or bytes.
3. **F4 (parallel calls)**: Document that parallel bash calls from the same turn are unsupported. Or investigate why the health check breaks them.
4. **Operational**: The grandchild leak (kill/README §"What does NOT happen") is accurately documented and is a real concern for production use. Consider adding `preexec_fn=os.setsid` + `os.killpg` in a future version.

---

## 9. Code References (附录：所触代码锚点)

> 以下行号来自 README Source 表。修 F1-F3 时可直取，无需重觅。

| Finding | 需改之文件 | 行号 | 函数/常量 | 说明 |
|---------|-----------|------|----------|------|
| F1+F2 截断格式与单位 | `lingtai/core/bash/__init__.py` | 200-203 | 输出截断逻辑 | README 言 `"\n... (truncated, {total} chars total)"`，实际 `[truncated — N bytes total]`。需验是 bytes 截断还是 chars 截断，同步后缀格式。 |
| F3 相对路径 sandbox | `lingtai/core/bash/__init__.py` | 176-187 | `BashManager.handle()` — 路径校验 | `Path(cwd).resolve()` 以进程 CWD 为基准。修法：`resolved = str(Path(self._working_dir).join(cwd).resolve())` 或先验 absolute 再 resolve。 |
| F3 错误信息 | `lingtai/core/bash/__init__.py` | 186-187 | 错误返回分支 | 错误信息 "must be under agent working directory" 未区分「路径不在沙箱」与「相对路径解析失败」，致误导。 |
| F4 并发 bash | 非 bash 模块 | — | kernel health_check | 需查 kernel runtime-loop 中 `health_check:pre_send_pairing` 逻辑，确认是否有意限制并行 tool call。 |

**其他相关代码锚点**（未出错，但修时可能涉及）：

| 行号 | 函数 | 说明 |
|------|------|------|
| 55-143 | `BashPolicy` 类 | 策略核心——`is_allowed()`, `_extract_commands()`, `describe()`, `yolo()` |
| 81-83 | `BashPolicy.yolo()` | 返回 allow=None, deny=None 的空策略 |
| 146-214 | `BashManager` 类 | 整个 bash 处理器——init, handle, 完整生命周期 |
| 190-197 | `subprocess.run()` 调用 | 执行核心——shell=True, capture_output, timeout |
| 211-212 | `TimeoutExpired` catch | 超时捕获分支 |
| 217-255 | `setup()` 入口 | 三种模式分发（yolo / policy_file / default） |
| `bash_policy.json` | 默认策略 | denylist 文件——yolo bypass 之物 |

---

## 10. Test Experience Notes

- **Sequential execution was required.** The initial attempt to batch 4 independent bash calls in one turn failed catastrophically (all 4 returned health_check errors). This forced a one-at-a-time approach, tripling the time needed.
- **The sandbox error message is misleading.** When `./logs` was rejected, the error said "must be under agent working directory" — implying the path is outside the sandbox. In reality, the path IS inside but resolves wrong. The user has no way to know the real issue is relative-path resolution.
- **Truncation is invisible in practice.** The 50,000-char cap is generous; most bash outputs won't hit it. But when it does fire, the suffix format mismatch means downstream parsers expecting the documented format will break.
- **The kill behavior is clean.** No surprises — timeout works exactly as documented. The only gotcha is the grandchild leak, which is well-documented.
