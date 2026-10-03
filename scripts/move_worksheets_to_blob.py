"""Move PDFs from the local worksheets/ folder into Azure Blob Storage.

Each file is stored under its own filename at the container root. Blobs are
not placed under a teacher or a student, and no worksheets row is created.

A local file is deleted only after the uploaded blob reports the same size.
"""
import os
import sys

import blobStorage

SOURCE_DIR = "worksheets"
MIN_BYTES = 100


def main():
    if not os.path.isdir(SOURCE_DIR):
        print(f"No {SOURCE_DIR}/ folder to move.")
        return 0

    names = sorted(
        name for name in os.listdir(SOURCE_DIR)
        if name.lower().endswith(".pdf") and os.path.isfile(os.path.join(SOURCE_DIR, name))
    )
    if not names:
        print(f"No PDFs in {SOURCE_DIR}/.")
        return 0

    container = blobStorage._container_client()
    moved = 0
    for name in names:
        path = os.path.join(SOURCE_DIR, name)
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            header = handle.read(5)
        if header != b"%PDF-" or size < MIN_BYTES:
            print(f"skip  {name}  ({size} bytes, not a complete PDF)")
            continue

        blob = container.get_blob_client(name)
        if blob.exists() and blob.get_blob_properties().size == size:
            os.remove(path)
            print(f"already stored, removed local  {name}")
            moved += 1
            continue

        with open(path, "rb") as handle:
            blobStorage.upload_pdf(name, handle.read())
        stored = blob.get_blob_properties().size
        if stored != size:
            print(f"FAIL  {name}  uploaded {stored} bytes, local file is {size}")
            return 1
        os.remove(path)
        print(f"moved  {name}")
        moved += 1

    print(f"Done. {moved} worksheet(s) are in blob storage.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
