import dataclasses
import datetime as dt
import time

import botocore
import humanize


@dataclasses.dataclass(eq=True, frozen=True)
class S3Entry:
    """Representation of S3 object."""

    name: str
    type: str
    size: str = ""
    date_modified: str = ""
    age: str = ""


LISTING_TTL = 60
LISTING_CACHE_SIZE = 256
_listings: dict[tuple, tuple[float, list[dict]]] = {}


def list_pages(s3_client: botocore.client.BaseClient, **params: str) -> list[dict]:
    key = tuple(sorted(params.items()))
    now = time.monotonic()
    hit = _listings.get(key)
    if hit and now - hit[0] < LISTING_TTL:
        return hit[1]
    pages = list(s3_client.get_paginator("list_objects_v2").paginate(**params))
    if len(_listings) >= LISTING_CACHE_SIZE:
        _listings.clear()
    _listings[key] = (now, pages)
    return pages


def folder_order(name: str) -> tuple:
    base = name.rstrip("/").rsplit("/", 1)[-1]
    parts = base.split(".")
    if all(part.isdigit() for part in parts):
        return (0, [-int(part) for part in parts], "")
    return (1, [], base.lower())


def file_entry(obj: dict, now: dt.datetime) -> S3Entry:
    return S3Entry(
        name=obj["Key"],
        type="file",
        size=humanize.naturalsize(obj["Size"]).replace("Bytes", "B").replace("Byte", "B"),
        date_modified=obj["LastModified"].strftime("%Y-%m-%d %H:%M UTC"),
        age=humanize.naturaltime(now - obj["LastModified"]),
    )


def file_order(entry: S3Entry) -> tuple:
    return (not entry.name.lower().endswith(".zip"), entry.name.lower())


def list_entries(s3_client: botocore.client.BaseClient, bucket: str, path: str, search: str = "") -> list[S3Entry]:
    params = {"Bucket": bucket, "Prefix": path}
    if not search:
        params["Delimiter"] = "/"
    search = search.lower()
    now = dt.datetime.now(dt.UTC)

    folders: set[str] = set()
    files: list[S3Entry] = []
    for page in list_pages(s3_client, **params):
        folders.update(p["Prefix"] for p in page.get("CommonPrefixes", []))
        for obj in page.get("Contents", []):
            key = obj["Key"]
            rel = key[len(path) :]
            if search and "/" in rel:
                folders.add(path + rel.split("/", 1)[0] + "/")
            if key.endswith("/") or search not in rel.lower():
                continue
            files.append(file_entry(obj, now))

    entries = [S3Entry(name=f, type="folder") for f in folders if search in f[len(path) :].lower()]
    entries.sort(key=lambda e: folder_order(e.name))
    files.sort(key=file_order)
    return entries + files
