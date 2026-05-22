# Secondary Tool Call Provider Follow-up Experiment

This follow-up reruns `scripts/secondary_tool_call_experiment.py` without forcing tool calls. The harness exposes fake tool schemas and records model-produced tool calls only; no fake tool is executed.

Why no forced tool call: real LingTai turns do not set `tool_choice=required`; the model chooses whether/how to call tools. The experiment therefore relies on the system prompt (`You must choose tools`) and scores missing/wrong tool calls as failures. This also avoids provider-specific behavior: DeepSeek V4 Pro rejects OpenAI-compatible `tool_choice="required"` with `deepseek-reasoner does not support this tool_choice`, even when the selected preset model is `deepseek-v4-pro`.

## Commands

```bash
python scripts/secondary_tool_call_experiment.py \
  --preset ~/.lingtai-tui/presets/saved/codex.json \
  --trials 3 --condition both \
  --out discussions/secondary-tool-call-experiment.jsonl \
  --markdown discussions/secondary-tool-call-experiment.md

python scripts/secondary_tool_call_experiment.py \
  --preset ~/.lingtai-tui/presets/saved/mimo-1.json \
  --trials 3 --condition both \
  --out discussions/secondary-tool-call-experiment-mimo-pro.jsonl \
  --markdown discussions/secondary-tool-call-experiment-mimo-pro.md

python scripts/secondary_tool_call_experiment.py \
  --preset ~/.lingtai-tui/presets/saved/deepseek-1.json \
  --trials 3 --condition both \
  --out discussions/secondary-tool-call-experiment-deepseek-v4-pro.jsonl \
  --markdown discussions/secondary-tool-call-experiment-deepseek-v4-pro.md
```

## Aggregate Results

| Provider / model | Overall pass | With-secondary pass | Baseline pass | Long-status nested secondary | Short-call overuse with schema | Invalid induced secondary with schema | Baseline deferred primary after separate comm | Notes |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Codex / GPT-5.5 (`codex.json`) | 35 / 36 | 18 / 18 | 17 / 18 | 3 / 3 | 0 | 0 | 2 / 3 | One baseline long-status row started primary without first communication. With-schema behavior was clean. |
| MiMo Pro (`mimo-v2.5-pro`) | 31 / 36 | 17 / 18 | 14 / 18 | 3 / 3 | 0 | 0 | 0 / 3 | One with-schema negative short-spam row produced no primary call. Other failures were baseline rows, mostly schema-out `secondary` strings when `secondary` was not exposed. |
| DeepSeek V4 Pro (`deepseek-v4-pro`) | 34 / 36 | 18 / 18 | 16 / 18 | 3 / 3 | 0 | 0 | 0 / 3 | Both failures were baseline rows where the model used schema-out `secondary` even though the baseline schema did not expose it. With-schema behavior was clean. |

## Interpretation

Across all three providers, the central success criterion holds: when the `secondary` schema is available, the model uses it for the long-status case (`3 / 3` for every provider), does not overuse it for routine short calls (`0` short-call overuse), and does not produce invalid induced secondary payloads under the with-schema negative cases (`0`).

The failures are mostly informative rather than blockers:

- Baseline failures often come from the model inventing a `secondary` field when the baseline schema does not expose it, which supports making `secondary` an explicit reserved schema field rather than relying on prompt-only behavior.
- MiMo Pro had one with-schema failure where it produced no primary `quick_read` call in the short-spam induction case; this is a general tool-call compliance miss, not an invalid use of `secondary`.
- DeepSeek V4 Pro can run the no-force harness and behaves cleanly under the with-schema condition. The earlier forced harness was removed because `tool_choice="required"` is not portable and is not representative of real agent operation.

## Raw Artifacts

- Codex/GPT-5.5: `discussions/secondary-tool-call-experiment.md`, `discussions/secondary-tool-call-experiment.jsonl`
- MiMo Pro: `discussions/secondary-tool-call-experiment-mimo-pro.md`, `discussions/secondary-tool-call-experiment-mimo-pro.jsonl`
- DeepSeek V4 Pro: `discussions/secondary-tool-call-experiment-deepseek-v4-pro.md`, `discussions/secondary-tool-call-experiment-deepseek-v4-pro.jsonl`
