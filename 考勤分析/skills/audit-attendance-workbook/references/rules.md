# Attendance rule contract

## Screening

- Require four header rows, people from row 5, T-Z exception summaries, and daily results from AG.
- Validate the exact T-Z headers before using them.
- Select a person when any T-Z value is positive.
- Also select a populated daily cell when its fill is not configured as white/no-fill.
- Run a lightweight string screen across remaining daily cells for explicit anomaly keywords and `未排班` with a real punch. Do not fully parse non-candidates.
- Parse all daily cells only for a T-Z-selected person. For a color-only person, parse the colored cell plus at most one neighboring date on each side for context.
- Treat color and keyword matches as screening signals, never as proof of an anomaly.

## Fixed shifts

For a configured fixed shift:

```text
arrival_delay = max(0, actual_in - scheduled_in)
late_minutes = max(0, arrival_delay - grace_minutes)
required_out = scheduled_out + arrival_delay
early_minutes = max(0, required_out - actual_out)
```

Early arrival does not allow an earlier checkout. Suppress same-day missing-checkout or early-departure findings until the configured end time has passed.

## Automatic shift correction

Compare complete punch pairs with every configured fixed shift. Penalize late arrival and early departure more heavily than early arrival and late departure.

Automatically change the shift only when all conditions hold:

- another fixed shift passes the configured score and improvement thresholds;
- the source status contains lateness or early departure;
- the assigned shift calculation also produces a timing anomaly;
- the candidate shift calculation is normal;
- the punch suffix remains unchanged.

Example: an A-shift late record with punches `10:00,21:00` can become a normal B-shift record when the configured thresholds are met. Queue near-ties or contradictory cases instead of changing them.

## Authorized events and flexible shifts

- Exclude recognized leave, travel, and outside-work records when they contain no explicit anomaly.
- When an approval and an explicit anomaly coexist, keep the day in the anomaly workbook for coverage review.
- Do not calculate fixed times for a flexible shift. Preserve its explicit missing-punch, lateness, early-departure, or absence status as a review finding.

## Attendance-day alignment

- Validate that column R is `出勤天数` and use its exported value as the baseline.
- After all deterministic and accepted Agent corrections, count each daily result explicitly recorded as a single-side missing punch as one missing-punch date and deduct 0.5 day. The R-alignment step does not infer, classify, recount, or deduct `旷工`; that status is owned by the earlier anomaly and T-Z stage.
- Sum the final monthly late and early-departure minutes, divide by 450 minutes per workday, and round the deduction upward to the next 0.1 day.
- Set aligned attendance days to `max(0, baseline - missing-punch deduction - time deduction)` and record the calculation in the R-cell comment and aggregate CLI counts, not in the dated anomaly workbook.
- Reuse the baseline stored in an existing audit comment so rerunning the skill does not deduct twice.
- If R is nonnumeric, a relevant daily item is unresolved, a missing-punch status is not explicitly single-side, or late/early text has no calculable minutes, keep R unchanged and add a human-review row. Do not send the monthly calculation to the model.

## Output

- Correct deterministic daily results and align R in an original-format copy, adding audit comments without changing cell styles.
- Recalculate T-Z from final daily statuses only for rows without unresolved Agent items.
- Include only findings with an exact date in the anomaly workbook: deterministic daily anomalies, automatic shift corrections, unresolved daily text, ambiguous shifts, and unscheduled punches.
- Group the anomaly workbook to one row per person. Within the row, sort dates ascending and combine same-day findings.
- Put the full original daily attendance cell beside each dated anomaly, preserving shift/status, events, and the final punch suffix. Compact internal line breaks so each date occupies one aligned line across `异常日期与类型`, `原始考勤数据`, `处理结果`, and `待人工确认`.
- Keep the anomaly sheet as a styled range with one worksheet-level auto-filter. Do not create a structured table over the same range.
- Exclude monthly summary reconciliation and R-column alignment records from the anomaly workbook and LLM summary; keep their audit trail in corrected-workbook comments and CLI counts.
- Write a minimal LLM summary containing only name, exact date, anomaly detail, and handling. The Agent must use that summary for its user-facing list and must not read the workbooks or `analysis.json`.
- Show automatic corrections separately from items that still need human review.
- Return A/B shift correction and affected-person counts. If corrections exceed 30, generate a Markdown report containing the Python-produced person/date detail instead of placing those details in model context.
