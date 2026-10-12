# Portable MarkItDown converters

Prometheus uses the unmodified DOCX, PPTX, XLSX and PDF converter classes from
Microsoft MarkItDown 0.1.8. The adapter loads only the requested converter,
without executing the generic MarkItDown package initializer, automatic format
detection, plugins, cloud services, image descriptions or external programs.
Magika, ONNX and their dependencies are intentionally not required by this path.

The original pinned wheels are retained here. LOCK.json records their PyPI
download URLs, verified SHA-256 hashes, versions and embedded license files.
Each wheel preserves its upstream license and notices, including dependency
and native-library notices. The MarkItDown license, omitted from its published
wheel, is included separately from its pinned v0.1.8 Git revision. Cobble's
upstream distribution carries its BSD-2-Clause declaration and author in wheel
METADATA rather than a separate license file. No wheel contents were modified.

These artifacts target the SSD's CPython 3.12 Windows x64 runtime. Each import
verifies the required wheels and extracts only those format dependencies into
a new host temporary directory. The directory is removed when parsing ends.
No pip installation, administrator access, host Office installation or internet
connection is required on another compatible Windows computer.

Imports retain the original 8 MiB / 400,000-character limits, with additional
Office ZIP/XML limits, 100 PDF pages, 100 PowerPoint slides, bounded spreadsheet
dimensions, 512 MiB worker memory and a 45-second deadline. The worker denies
network sockets and process creation. Macros, external resources, embedded
executables, encrypted files and scanned-document OCR are not supported.
