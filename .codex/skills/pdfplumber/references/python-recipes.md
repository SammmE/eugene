# pdfplumber Python Recipes

## Text Extraction Modes

```python
import pdfplumber

with pdfplumber.open("input.pdf") as pdf:
    page = pdf.pages[0]

    simple = page.extract_text()  # fast baseline
    layout = page.extract_text(
        layout=True,
        x_density=7.25,
        y_density=13,
    )  # layout-preserving
```

Use `layout=False` first. Move to `layout=True` for reports where spacing and line layout matter.

## Word-Level Extraction and Search

```python
with pdfplumber.open("input.pdf") as pdf:
    page = pdf.pages[0]
    words = page.extract_words(
        x_tolerance=3,
        y_tolerance=3,
        keep_blank_chars=False,
        return_chars=True,
    )

    matches = page.search(r"Invoice\s+#\d+", regex=True, return_chars=True)
```

Use word and search APIs when downstream logic needs coordinates.

## Cropping and Filtering

```python
bbox = (40, 120, 560, 730)  # (x0, top, x1, bottom)
region = page.crop(bbox)
clean = region.filter(lambda obj: obj.get("object_type") != "rect")
text = clean.extract_text()
```

Apply region reduction before table detection or text extraction on noisy pages.

## Table Extraction with Settings

```python
table_settings = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "intersection_tolerance": 3,
    "text_x_tolerance": 3,
    "text_y_tolerance": 3,
}

with pdfplumber.open("input.pdf") as pdf:
    page = pdf.pages[0].crop((30, 120, 580, 720))
    table = page.extract_table(table_settings=table_settings)
    tables = page.extract_tables(table_settings=table_settings)
```

If explicit ruling lines are weak, switch one or both strategies to `"text"`.

## Visual Table Debugging

```python
with pdfplumber.open("input.pdf") as pdf:
    page = pdf.pages[0]
    img = page.to_image(resolution=150)
    img.debug_tablefinder(table_settings={}).save("debug-tablefinder.png")
```

Use this while tuning `table_settings` to see line snapping and intersection behavior.

## Forms (AcroForm Values)

`pdfplumber` does not expose high-level form APIs, but values are accessible via `pdfplumber` + `pdfminer` internals:

```python
import pdfplumber
from pdfplumber.utils.pdfinternals import resolve, resolve_and_decode


def parse_field_helper(output, field, prefix=None):
    resolved_field = field.resolve()
    field_name = ".".join(
        filter(lambda x: x, [prefix, resolve_and_decode(resolved_field.get("T"))])
    )
    if "Kids" in resolved_field:
        for kid in resolved_field["Kids"]:
            parse_field_helper(output, kid, prefix=field_name)
    if "T" in resolved_field or "TU" in resolved_field:
        alt = resolve_and_decode(resolved_field.get("TU")) if resolved_field.get("TU") else None
        value = resolve_and_decode(resolved_field["V"]) if "V" in resolved_field else None
        output.append([field_name, alt, value])


with pdfplumber.open("document_with_form.pdf") as pdf:
    fields = resolve(resolve(pdf.doc.catalog["AcroForm"])["Fields"])
    form_data = []
    for field in fields:
        parse_field_helper(form_data, field)
```

## Structure Tree

Use page `structure_tree` when tagged PDFs expose semantic hints:

```python
with pdfplumber.open("input.pdf") as pdf:
    page = pdf.pages[0]
    for element in page.structure_tree:
        print(element.get("type"), element.get("mcids"))
```

Treat structure metadata as optional and inconsistent across documents.

## Repair Malformed PDFs

```python
import pdfplumber

with pdfplumber.open("broken.pdf", repair=True) as pdf:
    print(pdf.pages[0].extract_text())

# or save repaired bytes to disk
pdfplumber.repair("broken.pdf", outfile="repaired.pdf")
```

Use `gs_path=...` if Ghostscript is not auto-detected.

## Memory and Batch Processing

```python
import pdfplumber

with pdfplumber.open("large.pdf") as pdf:
    for page in pdf.pages:
        _ = page.extract_text()
        page.close()  # flush page-level cache
```

Close pages in long runs to reduce memory pressure.
