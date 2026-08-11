---
name: generate-process-report
description: Generate customer-facing process reports from MES-exported .xlsx workbooks containing “设备参数详情” and “文件绝对路径”. Use when Codex must parse JSON-like equipment parameters, collect representative images from URLs, local files, ZIP/TAR/RAR/7z archives or nested directories, and present each process row with its own image batch. Enforce a mandatory Markdown-first review followed by explicit user approval before creating any polished self-contained HTML.
---

# Generate Process Report

Follow the two-stage gate without exception.

## Stage 1: Generate Markdown

1. Inspect only workbook dimensions, sheet names, required headers, and compact statistics. Never paste a large workbook into model context.
2. Run `scripts/generate_report.py` with Markdown output. Use the bundled Python runtime when available.
3. Return the `.md` file and its sibling `_assets` directory to the user. State any attachment failures compactly.
4. Stop and ask whether to create the polished HTML version. Do not generate HTML in the same turn unless the user had already reviewed a Markdown output and explicitly approved HTML afterward.

```powershell
python scripts/generate_report.py input.xlsx --output output/process-report.md
```

## Stage 2: Generate HTML Only After Approval

Require an explicit user approval received after Stage 1. Then run with both `--format html` and `--approved-html`. The script rejects HTML without the approval flag.

```powershell
python scripts/generate_report.py input.xlsx --output output/process-report.html --format html --approved-html
```

Return the single `.html` file. It contains all styles, scripts, thumbnails, enlarged images, and a top navigation list ordered by `工步描述`; show its first row by default and reveal the remaining rows only through the expand button. It requires no CDN or sidecar files.

## Operating Rules

- Locate required fields by header text, not fixed column letters. Accept reordered columns.
- Treat `产品规格` as an internal-only denied field. Discard its header before building the column map; never access, parse, log, or render its row values.
- Keep image count at 10 or fewer per process. Prioritize every `display`-prefixed filename when the limit permits, then sample the naturally sorted beginning, middle, and end.
- Hide source URLs and absolute paths from customer-facing output by default.
- Treat per-row parameter, download, archive, or image failures as warnings and continue. Fail immediately when the workbook or required headers are invalid.
- Allow temporary downloads only inside the system temporary directory and let the script remove them at exit.
- Do not ask an agent to parse row data unless the deterministic script reports an unsupported structure requiring judgment.
- Read [references/report-contract.md](references/report-contract.md) only when changing parsing, sampling, privacy, or failure behavior.

## Verification

- Confirm the Markdown file opens and all relative image paths exist before returning Stage 1.
- Confirm HTML contains no `http://`, `https://`, external stylesheet, or external script references before returning Stage 2.
- Confirm neither Markdown nor HTML contains the `产品规格` field or any value sourced from that column.
- Run `python -m unittest discover -s tests -v` after changing the implementation.
