# Anatomy Leaves — Verification Convention

> *"行胜于言，亦胜于信。"*

## 三层防线

| 层 | 何时触发 | 动作 |
|---|---|---|
| **1. 开发者自检** | 改 daemon 源码后、提交前 | `pytest tests/test_daemon_leaves.py -v` |
| **2. CI 拦截** | PR 触及 `core/daemon/**` 或 `token_ledger.py` | `.github/workflows/leaves.yml` 自动跑 |
| **3. 脚本巡检** | 不定期 | `python3 verify_daemon_leaves.py src/lingtai` |

## 规则：先问文档，再改脚本

当 `test_daemon_leaves.py` 失败时：

```
文档说的 ≠ 代码做的
                ↓
        是代码故意改的吗？
           ╱          ╲
         是              否
          ↓               ↓
   更新文档和测试      修 bug
   使测试追上代码    使代码合乎文档
```

**禁止**：为了让测试绿而改脚本使断言变松。断言失败是信号，不是噪音。

## `_EXEMPT` 管理

`_EXEMPT` 是覆盖率守卫的豁免名单——标记"不需要叶子覆盖"的符号。无理由的豁免即后门。

**添入规则**：
1. 每条必须附**行内注释**说明为何豁免（PR 描述会沉，代码不会）
2. 不可自合——需 reviewer 批准
3. 脚本自报 `_EXEMPT` 总数——数字变了，diff 即可见

**季度清查**：
- 每季度 grep `_EXEMPT`，逐条审视理由是否仍成立
- 理由已失效者（如该符号变得复杂），削之并补叶或补测试
- 清查记录留于 PR，不可只在聊天中

## 文件索引

```
leaves/capabilities/daemon/
├── README.md                    ← 你在读的这个
├── verify_daemon_leaves.py      ← 独立脚本（50 项静态检查）
├── dual-ledger/                 ← 叶：token 双写归因
│   ├── README.md
│   └── test.md                  ← 运行时验证步骤（需 agent）
├── followup-injection/          ← 叶：结果回注三通道
│   ├── README.md
│   └── test.md
├── pre-send-health/             ← 叶：派发前验证级联
│   ├── README.md
│   └── test.md
└── max-rpm-gating/              ← 叶：并发上限
    ├── README.md
    └── test.md

tests/test_daemon_leaves.py      ← pytest 集成版（同 50 项，零依赖）
.github/workflows/leaves.yml     ← CI 自动拦截
```

## 覆盖范围

47 项静态检查 + 3 项覆盖率守卫，按叶分：

| 叶 | 检查数 | 覆盖内容 |
|---|---|---|
| dual-ledger | 13 | 双写路径、标签、零跳、容错 |
| followup-injection | 11 | inbox 注入、前缀格式、锁、截断常量 |
| pre-send-health | 12 | 黑名单、mkdir 标志、心跳、run_id 唯一性 |
| max-rpm-gating | 11 | 默认值、容量算术、回收、看门狗时序 |
| **coverage guard** | **3** | DaemonManager + DaemonRunDir 无未覆盖符号 + 豁免计数稳定 |

**不覆盖**（需运行时 agent，见各 test.md）：
- 通知是否实际到达 inbox
- 状态流转是否符合生命周期
- 看门狗是否实际杀死超时进程

## 给未来修改者的话

你改了 daemon 代码，测试红了。不要第一反应改测试。

1. 读 README 中对应的 claim。
2. 问自己：这个行为变了是有意的吗？
3. 如果是：更新 README claim + 更新测试断言 + 在 commit message 中说明行为变更。
4. 如果不是：修你的代码，别动测试。
