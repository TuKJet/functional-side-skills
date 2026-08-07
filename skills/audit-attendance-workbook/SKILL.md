---
name: audit-attendance-workbook
description: Audit and align a monthly attendance .xlsx with four header rows, R attendance days, T-Z exception summaries, and daily records from AG onward. Use when Codex must find missing punches, lateness, early departure, absence, colored special records, or fixed-shift misclassification; local Python corrects deterministic A/B shift cases, aligns R from final daily statuses, and returns an original-format aligned workbook, a one-person-per-row dated anomaly workbook, and a minimal LLM-ready anomaly summary.
---

# Audit attendance workbook

Run the local Python CLI from this directory. Never read the workbook or `analysis.json` into model context.

## Installation contract

Treat this folder as a self-contained, globally installable Skill. Install repository path
`skills/audit-attendance-workbook` with the built-in `skill-installer`, then run `uv sync --locked`
inside the installed directory. The installer targets `$CODEX_HOME/skills/audit-attendance-workbook`
and must not overwrite an existing directory without the user's explicit update decision. Tell the user
the newly installed Skill becomes discoverable on the next turn.

When developing from a repository checkout, run the same dependency command in this folder. Do not copy
the repository's workbooks, work directories, outputs, caches, or virtual environment into the installation.

## Workflow

1. Prepare:

   ```powershell
   uv sync --frozen
   uv run --frozen attendance-audit prepare --input <input.xlsx> --work-dir <work-dir>
   ```

2. If `agent_review_items` is positive, read `agent_review_queue.json` as an index. Open one listed `item_file` at a time, follow `references/agent-fallback-schema.md`, and write `agent_decisions.json`. Do not combine item files in one prompt.

3. Finalize:

   ```powershell
   uv run --frozen attendance-audit finalize \
     --analysis <work-dir>/analysis.json \
     --decisions <work-dir>/agent_decisions.json \
     --output-dir <output-dir>
   ```

   Omit `--decisions` when the queue is empty.

4. Read only the generated `llm_summary_file`, never the workbook or `analysis.json`. Report every item directly as `姓名｜日期｜异常｜处理`. Return the original-format aligned workbook and the anomaly workbook. Report the Python-produced A/B shift correction count. When `shift_correction_report` is non-null, also return that Markdown file.

Python owns screening, attendance calculations, approved-event exclusion, deterministic shift correction, T-Z reconciliation, and R-column alignment. Do not reproduce those decisions in the prompt. Read `references/rules.md` only when explaining or changing rule behavior.

## Guardrails

- Stop on `SCHEMA_MISMATCH`; do not guess another workbook layout.
- Never overwrite the input file or alter the punch suffix.
- Do not invent punches, approvals, leave, travel, or overtime.
- In the R-alignment step, process only recorded single-side missing punches and final late/early minutes. Do not infer or process `旷工`; keep the result from the earlier anomaly and T-Z stage.
- Keep personnel and organization metadata out of Agent files.
- The final `llm_summary_file` is the only model-readable named output. It contains only name, date, anomaly detail, and handling; do not enrich it with employee ID, department, position, attendance group, or unrelated dates.
- Keep the anomaly workbook one person per row. Combine multiple dated anomalies into wrapped multi-line cells and exclude all rows without an exact date.
- Use a worksheet-level auto-filter only. Do not add an overlapping structured Excel table, which can make desktop Excel repair `table1.xml` on open.
- Keep monthly T-Z reconciliation and R-column alignment out of the anomaly workbook and LLM summary. Record them only in corrected-workbook comments and aggregate CLI counts.
- Preserve workbook structure and formatting, verify the source hash, and reopen both outputs before reporting success.
