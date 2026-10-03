"""Move every blob from the production container into the dev container.

Worksheets saved before the split live in AZURE_STORAGE_PROD_CONTAINER
(worksheet). Local development reads AZURE_STORAGE_DEV_CONTAINER (devsheets).
Each blob keeps its name. The source is deleted only after the copy reports
the same size.

Run from the project root:

    PYTHONPATH=. ./venv/bin/python scripts/move_worksheets_to_dev.py
"""
import sys

from azure.core.exceptions import ResourceExistsError

import blobStorage


def main():
    source_name = blobStorage._env_text("AZURE_STORAGE_PROD_CONTAINER") or "worksheet"
    dest_name = blobStorage._env_text("AZURE_STORAGE_DEV_CONTAINER") or "devsheets"
    if source_name == dest_name:
        print(f"Source and destination are both {source_name}. Nothing to move.")
        return 1

    source = blobStorage.client_for(source_name)
    dest = blobStorage.client_for(dest_name)
    try:
        dest.create_container()
        print(f"Created container {dest_name}.")
    except ResourceExistsError:
        pass

    names = sorted(blob.name for blob in source.list_blobs())
    if not names:
        print(f"No blobs in {source_name}.")
        return 0

    moved = 0
    for name in names:
        src = source.get_blob_client(name)
        size = src.get_blob_properties().size
        dst = dest.get_blob_client(name)
        if dst.exists() and dst.get_blob_properties().size == size:
            src.delete_blob()
            print(f"already stored, removed source  {name}")
            moved += 1
            continue

        props = src.get_blob_properties()
        data = src.download_blob().readall()
        dst.upload_blob(data, overwrite=True, content_settings=props.content_settings)
        stored = dst.get_blob_properties().size
        if stored != size:
            print(f"FAIL  {name}  copied {stored} bytes, source is {size}")
            return 1
        src.delete_blob()
        print(f"moved  {name}")
        moved += 1

    print(f"Done. {moved} worksheet(s) are in {dest_name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
