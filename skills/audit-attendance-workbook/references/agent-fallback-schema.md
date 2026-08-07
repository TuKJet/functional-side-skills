# Agent fallback decision contract

Read `agent_review_queue.json` only as an index. Open one listed `item_file` at a time and discard it from context before reading the next item. Never read `analysis.json`.

Each item contains only a cell/date identifier and one short problem:

- `unsupported_text`: a capped raw cell, up to three unknown tokens, punches, and known parsed fields;
- `shift_misclassification`: assigned/candidate shift IDs, scores, proposed text, and at most the previous and next relevant dates.

Items exclude names, employee IDs, departments, positions, attendance groups, and unrelated dates.

Create one decision file:

```json
{
  "schema_version": "2.0",
  "decisions": [
    {
      "review_id": "Sheet!AG5:SHIFT_MISCLASSIFICATION",
      "action": "accept_suggestion",
      "reason": "Candidate checkout and adjacent shift IDs agree.",
      "confidence": 0.93
    }
  ]
}
```

Allowed actions:

- `keep`: preserve the current value and leave it for human review.
- `accept_suggestion`: use the provided `candidate.proposed_text`.
- `replace`: provide `corrected_text` for unsupported text.

For `replace`, copy the original final punch suffix exactly. Never invent punches, leave, travel, outside work, overtime, makeup-card records, or approval coverage. Use `keep` when the short item is insufficient.
