# Report contract

## Workbook

- Accept `.xlsx` only and read it with `openpyxl` in read-only, data-only mode.
- Require the headers `设备参数详情` and `文件绝对路径` on the configured header row.
- Treat each non-empty data row as one process section. Use optional MES context columns for titles and metadata without making them required.
- Apply the privacy denylist while reading the header row. Discard `产品规格` before building the column-index map, and never access or parse values from that column.
- Parse parameters in this order: strict JSON, Python literal objects, simple line/comma/semicolon-separated key-value text, then raw text with a warning.

## Attachments and sampling

- Split attachment cells on line breaks or semicolons.
- Accept HTTP(S) URLs, local images, local directories, and supported archives.
- Read ZIP and TAR members without bulk extraction. Use optional `rarfile` and `py7zr` support for RAR and 7z; warn and continue when a decoder is unavailable.
- Traverse nested archive member paths and local directories recursively. Reject unsafe archive member paths.
- Natural-sort image names. If at least one basename begins with `display`, case-insensitively, restrict both selection and failure fallback to `display` images only; do not fill unused slots with ordinary images. If no `display` image exists, fill the limit with evenly distributed candidates so the beginning, middle, and end are represented.
- If more than 10 `display` images exist, keep the first 10 in natural order and record the unavoidable limit conflict.
- Re-encode rendered images as JPEG to normalize orientation, strip metadata, reduce size, and avoid unsafe active image content.

## Deliverables and approval gate

1. Generate Markdown plus a sibling `<report-stem>_assets/` directory.
2. Return the Markdown result and ask for explicit approval.
3. Generate HTML only in a later turn after approval. Require `--approved-html` at the CLI.
4. Produce HTML as one offline file with embedded CSS, JavaScript, thumbnails, and enlarged images.
5. Place the navigation list on its own row in workbook order. Show its first grid row by default and reveal the remaining rows with a button. Label each link with its sequence and `工步描述`, target a unique process anchor, return to one visible row after selection, and highlight the currently viewed process. Do not use horizontal scrolling.

## Privacy and limits

- Do not print or render internal URLs and absolute paths by default.
- Do not include `产品规格` in in-memory records, logs, Markdown, HTML, diagnostics, or error text.
- Clean temporary downloads automatically.
- Default limits: 10 shown images per process, 512 MiB per downloaded source, 2 GiB total downloads, and 60 MiB per decoded image candidate.
- Continue past individual failures and summarize warnings by process; reject missing headers, unreadable workbooks, invalid output formats, or HTML without approval.
