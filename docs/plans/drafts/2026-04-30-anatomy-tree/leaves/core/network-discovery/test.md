---
timeout: 180
---

# Test: Network Discovery

## Setup

1. Locate a `.lingtai/` project directory containing at least two agents.
2. Identify `base_dir` = the `.lingtai/` directory (e.g. `~/.lingtai/<project>/`).

## Steps

1. **List agent directories** — each subdirectory with `.agent.json` is an agent.
   ```bash
   for d in <base_dir>/*/; do
     [ -f "$d/.agent.json" ] && echo "AGENT: $d" && python3 -c "import json; print(json.load(open('${d}.agent.json')))" 2>/dev/null
   done
   ```

2. **Validate `.agent.json` structure** — each manifest must have `address` and `agent_name`.
   ```bash
   for d in <base_dir>/*/; do
     [ -f "$d/.agent.json" ] && python3 -c "
   import json, sys
   m = json.load(open('$d/.agent.json'))
   assert 'address' in m, 'missing address'
   assert 'agent_name' in m, 'missing agent_name'
   print(f'OK: {m[\"agent_name\"]} @ {m[\"address\"]}')
   " 2>&1
   done
   ```

3. **Check avatar ledger** — verify `delegates/ledger.jsonl` exists for parent agents and has valid JSONL.
   ```bash
   for d in <base_dir>/*/; do
     ledger="$d/delegates/ledger.jsonl"
     [ -f "$ledger" ] && echo "LEDGER: $d" && python3 -c "
   [json.loads(l) for l in open('$ledger') if l.strip()]
   print(f'  {sum(1 for _ in open(\"$ledger\"))} records')
   "
   done
   ```

4. **Check contacts** — verify `mailbox/contacts.json` exists for agents that have contacts.
   ```bash
   for d in <base_dir>/*/; do
     contacts="$d/mailbox/contacts.json"
     [ -f "$contacts" ] && echo "CONTACTS: $d" && python3 -c "
   import json
   c = json.load(open('$contacts'))
   print(f'  {len(c)} contacts')
   "
   done
   ```

5. **Check mail structure** — verify `mailbox/inbox/` and `mailbox/sent/` directories exist.
   ```bash
   for d in <base_dir>/*/; do
     inbox=$(ls "$d/mailbox/inbox/" 2>/dev/null | wc -l)
     sent=$(ls "$d/mailbox/sent/" 2>/dev/null | wc -l)
     [ "$inbox" -gt 0 ] || [ "$sent" -gt 0 ] && echo "MAIL: $d (inbox:$inbox sent:$sent)"
   done
   ```

6. **Verify peer addressing** — confirm a relative name resolves to the expected absolute path.
   ```bash
   python3 -c "
   from pathlib import Path
   base = Path('<base_dir>')
   name = '<agent_relative_name>'
   resolved = base / name
   print(f'{name} -> {resolved}')
   assert (resolved / '.agent.json').exists()
   "
   ```

## Pass Criteria

- Every agent directory contains a valid `.agent.json` with `address` and `agent_name`.
- Avatar ledgers (where present) contain valid JSONL with `event`, `working_dir`, `name` fields.
- Contacts (where present) are valid JSON arrays of objects with `address` field.
- Mail directories (inbox/sent) exist for agents that have communicated.
- Relative name resolution produces a path containing `.agent.json`.

## Output Template

```
## Network Discovery Test Results

| Check | Result | Evidence |
|-------|--------|----------|
| Agents discovered | PASS/FAIL | <count> agents found |
| .agent.json valid | PASS/FAIL | <per-agent status> |
| Avatar ledgers valid | PASS/SKIP/FAIL | <per-ledger status> |
| Contacts valid | PASS/SKIP/FAIL | <per-agent status> |
| Mail structure | PASS/SKIP/FAIL | <inbox/sent counts> |
| Peer addressing | PASS/FAIL | <resolution example> |
```
