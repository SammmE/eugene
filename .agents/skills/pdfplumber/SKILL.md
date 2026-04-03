---
name: pdfplumber
description: Extract text, words, coordinates, tables, forms, and structure metadata from PDFs with Python `pdfplumber`. Use when tasks involve machine-generated PDF parsing, table extraction tuning, layout-aware text recovery, visual debugging, malformed PDF repair workflows, or integrating OCR fallback for scanned PDFs.
---

# pdfplumber

## Overview

Use this skill to implement robust PDF extraction flows in Python with `pdfplumber`, including object-level inspection (`chars`, `lines`, `rects`, `curves`, `images`), text and table extraction, visual diagnostics, and recovery for malformed PDFs.

## Workflow

1. Confirm PDF type before coding extraction logic.
Check whether the file is machine-generated (best for `pdfplumber`) or scanned image-based (requires OCR fallback).

2. Start with conservative text extraction.
Use `page.extract_text()` for baseline output, then switch to `extract_words()` or `layout=True` only if structure fidelity is needed.

3. Narrow scope early.
Use `crop(...)`, `within_bbox(...)`, `outside_bbox(...)`, or `filter(...)` on noisy pages before extracting text or tables.

4. Tune tables explicitly.
Use `extract_table(s)` with custom `table_settings` and iterate with `debug_tablefinder(...)` plus image overlays.

5. Add error handling and fallback strategy.
Use `repair=True` or `pdfplumber.repair(...)` for malformed files and OCR fallback when no meaningful character objects exist.

6. Control memory on large PDFs.
Close pages or the full PDF object after processing, especially in long-running batch jobs.

## Quick Start

```python
import pdfplumber

with pdfplumber.open("input.pdf") as pdf:
    page = pdf.pages[0]
    text = page.extract_text()
    words = page.extract_words()
    table = page.extract_table()
```

## Extraction Rules

- Prefer `extract_words()` when bounding boxes are needed.
- Use `extract_text(layout=True)` for fixed-width or layout-sensitive documents.
- Use `page.search(...)` for coordinate-aware lookup of regex/string matches.
- Call `page.dedupe_chars()` when duplicate glyph rendering causes repeated text.
- Use page-object lists (`chars`, `lines`, `rects`, `curves`, `images`, `annots`, `hyperlinks`) for geometry-driven logic.

## Table Rules

- Start with defaults, then tune:
`vertical_strategy`, `horizontal_strategy`, `snap_*`, `join_*`, `intersection_*`, `text_*`.
- Use `"text"` strategies when lines are missing but columns/rows align visually.
- Crop to likely table regions before extracting.
- Validate with `find_table(s)` when extraction quality is uncertain.

## Visual Debugging Rules

- Use `page.to_image(...)` with higher resolution (for example 150 DPI) during tuning.
- Overlay `debug_tablefinder(...)` output to inspect detected edges, intersections, and table boxes.
- Save debug images as artifacts when iterating extraction settings.

## Advanced Topics

Read [python-recipes.md](./references/python-recipes.md) for focused recipes on:
- table settings patterns
- form value extraction through pdfminer wrappers
- structure tree usage
- malformed PDF repair
- coordinate conversion notes
