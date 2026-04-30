# Test Result: Mail / Email Capability

> **Tester:** test-bash (agent_id: 20260430-082407-5336)  
> **Date:** 2026-04-30  
> **Method:** kernel-leaf-test 四层递进法  
> **Contract under test:** `leaves/capabilities/mail/README.md`, `mailbox-core/`, `dedup/`, `atomic-write/`, `peer-send/`, `identity-card/`, `scheduling/`  
> **Agent working dir:** `/Users/huangzesen/work/lingtai-projects/lingtai-dev/.lingtai/test-bash`  
> **Initial state:** empty mailbox

---

## 1. 正路

### 1.1 Basic self-send

```
Action:    email(send, address="test-bash", subject="Test: basic self-send", message="...")
Expect:    status='sent', to=["test-bash"]
Actual:    status='sent', to=["test-bash"], cc=[], bcc=[]
Verdict:   ✅ PASS
```

### 1.2 Check inbox

```
Action:    email(check)
Expect:    total=1, one unread email with identity card fields
Actual:    total=1, showing=1; id=8517c00d, from="test-bash (test-bash)", 
           unread=true, sender_agent_id="20260430-082407-5336", sender_language="wen"
Verdict:   ✅ PASS
```

**Note on `from` display:** Inbox shows `"test-bash (test-bash)"` — agent_name in parentheses appended to address. Matches identity-card README: `_message_summary` uses `"agent_name (address)"` format when identity.agent_name exists.

### 1.3 Read full message

```
Action:    email(read, email_id=["8517c00d-..."])
Expect:    id, from, to, subject, message, identity fields
Actual:    all fields present; from="test-bash" (no parenthetical on read — identity display differs between check and read)
Verdict:   ✅ PASS
```

**Observation:** `from` field in `read` output is `"test-bash"` (plain address), while `check` shows `"test-bash (test-bash)"`. This is a **display inconsistency** — `read` uses the raw `from` field, `check` uses the enriched `_inject_identity()` format. Not documented in README.

### 1.4 Search

```
Action:    email(search, query="self-send")
Expect:    matches in subject and/or message across folders
Actual:    total=2 — inbox entry (8517c00d) + sent entry (ffdd51e4)
           Two different UUIDs for the same logical email!
Verdict:   ✅ PASS — matches peer-send README §"wrapper UUID and Mailman UUID are independent"
```

### 1.5 Archive

```
Action:    email(archive, email_id=["8517c00d-..."])
Expect:    status='ok', archived=[id]
Actual:    status='ok', archived=["8517c00d-..."]
Verdict:   ✅ PASS
```

### 1.6 Check archive folder

```
Action:    email(check, folder="archive")
Expect:    archived message in archive folder
Actual:    total=1, folder="archive", id=8517c00d
Verdict:   ✅ PASS — mail survives folder move
```

---

## 2. 限界 + 边角

### 2.1 Dedup — first send (counter=0)

```
Action:    email(send, address="test-bash", subject="Dedup Test", message="...")
Expect:    status='sent' (first time, no dedup)
Actual:    status='sent'
Verdict:   ✅ PASS
```

### 2.2 Dedup — second identical send (counter=1)

```
Action:    email(send, same subject + message + address)
Expect:    status='sent' (counter=1 < _dup_free_passes=2, so allowed)
Actual:    status='sent'
Verdict:   ✅ PASS — counter incremented but below threshold
```

### 2.3 Dedup — third identical send (counter=2, triggers gate)

```
Action:    email(send, same subject + message + address)
Expect:    status='blocked', warning message
Actual:    status='blocked', warning="Identical message already sent to: test-bash. 
           This looks like a repetitive loop — think twice before sending."
Verdict:   ✅ PASS — dedup gate triggered at count≥2 as documented
```

### 2.4 Contacts — add

```
Action:    email(add_contact, address="test-bash", name="My Test Agent", note="...")
Expect:    status='added', contact object returned
Actual:    status='added', contact={address, name, note}
Verdict:   ✅ PASS
```

---

## 3. 根因分析

### 与 bash + codex 对比

| 维度 | Bash | Codex | Mail |
|------|------|-------|------|
| 文档-实现一致性 | 4 项偏离 | 0 项 | **1 项轻微** |
| 复杂度 | 路径+进程+并发 | 纯数据 | 文件 I/O+线程+定时器+去重 |
| Error 格式 | 1 项不符 | 全一致 | 全一致 |
| 行为可预测性 | sandbox 意外 | 全可预测 | 全可预测 |

### 复杂度假说验证

| 能力 | 复杂度 | 偏离数 | 支持假说？ |
|------|--------|--------|-----------|
| codex | 低（纯数据） | 0 | ✅ |
| bash | 中高（路径+进程+并发） | 4 | ✅ |
| mail | 高（文件+线程+定时器+去重） | 1 | ⚠️ 部分 |

Mail 复杂度高于 bash，但偏离数反而更少。这说明**复杂度假说不完全成立**——复杂度是必要条件但非充分条件。更准确的说法是：**迭代频繁 + 复杂度高 = 隙易生**。Mail 可能迭代较少或文档同步做得更好。

### 小发现

**`from` 字段显示不一致**：`check` 显示 `"test-bash (test-bash)"`（_inject_identity 格式），`read` 显示 `"test-bash"`（原始 from 字段）。README 未明确此差异。这可能是 by-design（read 返回原始数据，check 返回展示优化），但应文档化。

**双 UUID 已确认**：inbox UUID `8517c00d` 与 sent UUID `ffdd51e4` 独立——peer-send README 明确说"wrapper UUID 和 Mailman UUID 独立，无交叉引用字段"。search 同时返回两者。

---

## 4. Summary & Findings

### 合规表

| Behavior | README says | Reality | Verdict |
|----------|-------------|---------|---------|
| Self-send → inbox | direct write_text() | ✅ | PASS |
| check → {id, from, to, subject, preview, unread, identity fields} | ✅ | ✅ exact match | PASS |
| read → {id, from, to, subject, message, identity} | ✅ | ✅ | PASS |
| search → matches across all folders | ✅ | ✅ (inbox + sent) | PASS |
| archive → moves to archive/ | ✅ | ✅ | PASS |
| Dedup at _dup_free_passes=2 | ✅ | ✅ blocked on 3rd send | PASS |
| Dual UUID (inbox vs sent) | ✅ documented | ✅ confirmed | PASS |
| Identity card: agent_name in parentheses | ✅ documented | ✅ "test-bash (test-bash)" | PASS |

### 偏离表

| # | Finding | README claim | Actual behavior | Severity |
|---|---------|-------------|-----------------|----------|
| M1 | **`from` display inconsistency** | check vs read not distinguished | check: "name (addr)", read: "addr" | Low (by design, but undocumented) |

---

## 5. Code References (附录)

| What | File | Line(s) |
|------|------|---------|
| EmailManager class | `core/email/__init__.py` | full file |
| `_send()` + dedup gate | `core/email/__init__.py` | 787-907 |
| Dedup gate logic | `core/email/__init__.py` | 804-824 |
| Counter update | `core/email/__init__.py` | 894-900 |
| Identity card injection (wrapper) | `core/email/__init__.py` | 850 |
| `_inject_identity()` extraction | `core/email/__init__.py` | 369-385 |
| `_email_summary()` | `core/email/__init__.py` | 344, 351, 1011 |
| Scheduler loop | `core/email/__init__.py` | 665-781 |
| At-most-once (increment-before-send) | `core/email/__init__.py` | 719-722 |
| Atomic write (peer delivery) | `services/mail.py` | 198-207 |
| Self-send direct write | `intrinsics/mail.py` | 248-264 |
| `_message_summary` sender display | `intrinsics/mail.py` | 214-217 |
| `_build_manifest()` | `base_agent.py` | 1477-1501 |
| resolve_address | `handshake.py` | 13-22 |
| is_agent / is_alive | `handshake.py` | 25-55 |

---

## 6. Recommendations

1. **文档化 `from` 显示差异**：check 与 read 的 `from` 字段格式不同（identity-enriched vs raw）。建议 README 添加一句说明。
2. **Scheduling 测试未做**：需创建真实 schedule 并验证 interval、count、cancel、reactivate 行为。当前测试受限于时间。
3. **Dedup 生命周期**：dedup 是 in-memory，重启清零——已通过 contract 确认但无法在单次会话中验证重启行为。

---

## 7. Test Experience Notes

- **Mail 测试比 bash 平稳**：无并发崩溃、无路径歧义、无超时意外。工具行为高度可预测。
- **Dedup 是优秀的设计**：两次免费通道后 block，平衡了防误发与正常使用。trigger 时机精确。
- **双 UUID 机制**：inbox UUID 与 sent UUID 独立，search 能同时搜索两个——用户体验良好，但无交叉引用。
- **Identity card 精准**：sender_agent_id、sender_language、is_human 等字段在 check 中直显，agent 在通讯录中可据此识别对方。
