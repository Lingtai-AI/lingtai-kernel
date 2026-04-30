# Test Result: Codex Capability (Persistent Knowledge Store)

> **Tester:** test-bash (agent_id: 20260430-082407-5336)  
> **Date:** 2026-04-30  
> **Method:** kernel-leaf-test 四层递进法  
> **Contract under test:** `leaves/capabilities/shell/codex/README.md`  
> **Agent working dir:** `/Users/huangzesen/work/lingtai-projects/lingtai-dev/.lingtai/test-bash`  
> **Initial state:** empty codex (0 entries)

---

## 1. 正路

### 1.1 Basic submit

```
Action:    submit(title="Test Entry Alpha", summary="First test entry for codex audit",
                 content="This is the content of entry alpha...")
Expect:    status='ok', id=8-char hex, entries=1, max=20
Actual:    status='ok', id='1700280d', entries=1, max=20
Verdict:   ✅ PASS
```

### 1.2 Second submit

```
Action:    submit(title="Test Entry Beta", ...)
Expect:    entries=2
Actual:    status='ok', id='a0aa05cc', entries=2, max=20
Verdict:   ✅ PASS
```

### 1.3 Filter with pattern

```
Action:    filter(pattern="alpha")
Expect:    returns alpha entry only; fields: id + title + summary (no content)
Actual:    [{"id": "1700280d", "title": "Test Entry Alpha", "summary": "First test entry..."}]
Verdict:   ✅ PASS — regex search, IGNORECASE, correct field set
```

### 1.4 View (depth=content)

```
Action:    view(ids=["1700280d"])
Expect:    id + title + summary + content; no supplementary field
Actual:    {"id":"1700280d", "title":"...", "summary":"...", "content":"This is the content..."}
Verdict:   ✅ PASS
```

### 1.5 Export

```
Action:    export(ids=["1700280d"])
Expect:    status='ok', files=["exports/1700280d.txt"], count=1
Actual:    status='ok', files=["exports/1700280d.txt"], count=1
Verdict:   ✅ PASS
```

### 1.6 Consolidate

```
Action:    consolidate(ids=["1700280d","a0aa05cc"], title="Consolidated Alpha-Beta",
                       summary="Merged entry from alpha and beta test entries",
                       content="This entry consolidates alpha and beta...",
                       supplementary="Extended details...")
Expect:    status='ok', new id (8-char), removed=2
Actual:    status='ok', id='885d96e9', removed=2
Verdict:   ✅ PASS
```

### 1.7 View with depth=supplementary

```
Action:    view(ids=["885d96e9"], depth="supplementary")
Expect:    includes supplementary field
Actual:    ... "supplementary": "Extended details: both alpha and beta were test entries..."
Verdict:   ✅ PASS
```

---

## 2. 限界

### 2.1 Submit missing title

```
Action:    submit() — all fields empty
Expect:    error mentioning title is required
Actual:    {"error": "title is required for submit."}
Verdict:   ✅ PASS — field-by-field validation
```

### 2.2 Submit missing summary

```
Action:    submit(title="Test Gamma")
Expect:    error mentioning summary is required
Actual:    {"error": "summary is required for submit."}
Verdict:   ✅ PASS
```

### 2.3 Submit missing content

```
Action:    submit(title="Test Gamma", summary="Gamma summary")
Expect:    error mentioning content is required
Actual:    {"error": "content is required for submit."}
Verdict:   ✅ PASS
```

### 2.4 View with unknown ID

```
Action:    view(ids=["deadbeef"])
Expect:    error "Unknown codex IDs: deadbeef"
Actual:    {"error": "Unknown codex IDs: deadbeef"}
Verdict:   ✅ PASS
```

### 2.5 Delete with unknown ID

```
Action:    delete(ids=["deadbeef"])
Expect:    error "Unknown codex IDs: deadbeef"
Actual:    {"error": "Unknown codex IDs: deadbeef"}
Verdict:   ✅ PASS
```

### 2.6 Invalid regex in filter

```
Action:    filter(pattern="[\"")   — unterminated character set
Expect:    error "Invalid regex pattern: ..."
Actual:    {"error": "Invalid regex pattern: unterminated character set at position 0"}
Verdict:   ✅ PASS
```

### 2.7 Filter without pattern

```
Action:    filter()   — no pattern
Expect:    returns all entries (2 at this point)
Actual:    2 entries returned
Verdict:   ✅ PASS
```

### 2.8 Filter with limit

```
Action:    filter(limit=1)   — should return at most 1 entry
Expect:    1 entry
Actual:    [{"id": "885d96e9", ...}] — 1 entry
Verdict:   ✅ PASS
```

### 2.9 Delete (basic)

```
Action:    delete(ids=["06cb9209"])
Expect:    status='ok', removed=1
Actual:    status='ok', removed=1
Verdict:   ✅ PASS
```

### 2.10 Consolidate with unknown ID

```
Action:    consolidate(ids=["deadbeef"], ...)
Expect:    error "Unknown codex IDs"
Actual:    {"error": "Unknown codex IDs: deadbeef"}
Verdict:   ✅ PASS
```

### 2.11 Export with unknown ID

```
Action:    export(ids=["deadbeef"])
Expect:    error "Unknown codex IDs"
Actual:    {"error": "Unknown codex IDs: deadbeef"}
Verdict:   ✅ PASS
```

---

## 3. 边角

### 3.1 ID format

```
Contract:  "8-char sha256 prefix"
Observed:  '1700280d', 'a0aa05cc', '885d96e9', '06cb9209' — all 8 hex chars
Verdict:   ✅ PASS
```

### 3.2 Filter excludes content

```
Contract:  filter "returns id+title+summary only"
Actual:    No 'content' field in any filter result entry
Verdict:   ✅ PASS
```

### 3.3 Consolidate removes old, creates new

```
Contract:  "removes old entries → creates merged entry → atomic save"
Actual:    removed=2 on consolidate of 2 entries; subsequent filter shows only new merged entry
Verdict:   ✅ PASS
```

---

## 4. 并发

### 4.1 Concurrent submits

```
Not tested. Codex is a single-file JSON store with no documented concurrent
write protection beyond atomic tempfile→replace. Two simultaneous submits
could race on _load_entries() → _save_entries(). 
README does not document mutex behavior.
```

---

## 5. 根因分析

### 与 bash 测试对比

| 维度 | Bash | Codex |
|------|------|-------|
| 文档-实现一致性 | 4 项偏离 (F1-F4) | **0 项偏离** |
| Error 格式 | 1 项不符（截断后缀） | 全部一致 |
| 行为可预测性 | sandbox 相对路径意外 | 全部可预测 |

**结论：codex 实现与文档高度一致，无连贯性债务。** 这证明"文档-实现渐行渐远"不是系统性问题，而是 bash 特有的——可能是 bash 模块迭代更频繁，而 codex 较稳定。

此结果修正了 bash 测试报告 §7 的假设：四瑕归一不是全核同病，至少 codex 无此隙。

### 小发现

**`_inject_catalog()` 延迟更新**：submit/consolidate/delete 后，当轮上下文中的 `## codex` 系统提示节仍为空。catalog 更新的是下轮加载的系统提示，非当前上下文。README 未明确此行为，但这是实现细节层面的合理设计（避免 mid-turn system prompt mutation）。

---

## 6. Summary & Findings

### 合规表

| Behavior | README says | Reality | Verdict |
|----------|-------------|---------|---------|
| submit → {status, id, entries, max} | ✅ | ✅ exact match | PASS |
| filter → {status, entries: [{id, title, summary}]} | ✅ | ✅ | PASS |
| view depth=content → content, no supplementary | ✅ | ✅ | PASS |
| view depth=supplementary → includes supplementary | ✅ | ✅ | PASS |
| consolidate → remove old + create new | ✅ | ✅ removed=2 | PASS |
| export → {files, count} | ✅ | ✅ | PASS |
| delete → {removed} | ✅ | ✅ | PASS |
| Missing fields → error per field | ✅ | ✅ title→summary→content | PASS |
| Unknown IDs → error | ✅ | ✅ | PASS |
| Invalid regex → error | ✅ | ✅ | PASS |
| ID = 8-char sha256 prefix | ✅ | ✅ | PASS |
| Max entries = 20 | ✅ | ✅ (max=20 in submit return) | PASS |

### 偏离表

**无偏离。** Codex 是文档-实现一致性最好的能力之一。

---

## 7. Code References (附录)

| What | File | Line(s) |
|------|------|---------|
| `CodexManager` class + schema | `lingtai/core/codex/__init__.py` | 33-356 |
| `DEFAULT_MAX_ENTRIES = 20` | `lingtai/core/codex/__init__.py` | 85 |
| `_inject_catalog()` | `lingtai/core/codex/__init__.py` | 100-118 |
| `_load_entries()` / `_save_entries()` | `lingtai/core/codex/__init__.py` | 124-156 |
| `_make_id()` SHA-256 | `lingtai/core/codex/__init__.py` | 158-162 |
| `_submit()` / `_filter()` / `_view()` | `lingtai/core/codex/__init__.py` | 184-272 |
| `_consolidate()` / `_delete()` / `_export()` | `lingtai/core/codex/__init__.py` | 274-356 |
| `setup()` entry point | `lingtai/core/codex/__init__.py` | 359-372 |

---

## 8. Recommendations

1. **无需修代码或修文档。** Codex contract 与实现高度一致。
2. **可选：** README 添加一句 `_inject_catalog()` 的更新时机说明（下轮加载时生效，非 mid-turn）。
3. **并发测试未做。** 如计划支持多 agent 共享同一 codex（当前不支持），需补并发写入测试。

---

## 9. Test Experience Notes

- **Codex 测试速度远快于 bash。** 无并发崩溃问题，无 sandbox 路径歧义，每步一 tool call 即可。
- **Field-by-field validation 是好的设计。** 缺 title 报 title、缺 summary 报 summary，不一次性全报——符合"尽早报错"原则。
- **Codex 作为第二枚能力测试验证了模板的可用性。** `kernel-leaf-test` 四层递进法从 bash 移植到 codex 无缝——正路→限界→边角→并发，每层自然可填。
- **这修正了 bash 测试的根因假设。** Bash 四瑕归一不意味着全核同病。至少 codex 证明：稳定的模块可以做到文档-实现零偏差。
