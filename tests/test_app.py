import datetime as dt
import io
from unittest import mock

import botocore.exceptions
import pytest

from s3_web_browser import create_app, s3

KEYS = [
    "a/",
    "a/x.zip",
    "a/sub/y.img",
    "a/sub/deep/z.json",
    "a/Other/w.txt",
    "b/boot.img",
    "b/rom.zip",
    "rom/dev1/17/24.0/20261111/rom-20261111-dev1.zip",
    "rom/dev1/17/24.0/20261111/boot.img",
    "rom/dev1/17/24.0/20261105/rom-20261105-dev1.zip",
    "rom/dev2/16/23.2/20260901/boot.img",
    "rom/OTA_UPDATES/17/dev1.json",
    "rom/dev1/17/24.0/notes.txt",
    *(f"many/f{i:04}.zip" for i in range(2500)),
]
DATE = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def client_error(code):
    return botocore.exceptions.ClientError({"Error": {"Code": code}}, "op")


class FakeS3:
    def __init__(self):
        self.listings = 0

    def get_paginator(self, _):
        return self

    def paginate(self, Bucket, Prefix="", Delimiter=None):
        self.listings += 1
        if Bucket == "missing":
            raise client_error("NoSuchBucket")
        prefixes, contents = set(), []
        for key in (k for k in KEYS if k.startswith(Prefix)):
            rel = key[len(Prefix) :]
            if Delimiter and "/" in rel:
                prefixes.add(Prefix + rel.split("/", 1)[0] + "/")
            else:
                contents.append({"Key": key, "Size": 1234, "LastModified": DATE})
        for i in range(0, max(len(contents), 1), 1000):
            yield {
                "Contents": contents[i : i + 1000],
                "CommonPrefixes": [{"Prefix": p} for p in sorted(prefixes)] if i == 0 else [],
            }

    def list_objects_v2(self, Bucket, Delimiter):
        return next(self.paginate(Bucket, "", Delimiter))

    def get_object(self, Bucket, Key):
        if Key not in KEYS:
            raise client_error("NoSuchKey")
        return {"Body": io.BytesIO(b"{}")}

    def generate_presigned_url(self, *_, **__):
        return "https://s3.example/presigned"


@pytest.fixture
def fake():
    s3._listings.clear()
    return FakeS3()


@pytest.fixture
def client(fake):
    with mock.patch("boto3.client", return_value=fake):
        return create_app().test_client()


def get(client, url):
    r = client.get(url)
    return r, r.get_data(as_text=True)


def test_index(client):
    r, html = get(client, "/")
    assert r.status_code == 200
    assert "many" in html
    assert "2500 items" in html
    assert "rom-" not in html


def test_browse_lists_one_level(client, fake):
    _, html = get(client, "/a/")
    assert "sub/" in html
    assert "Other/" in html
    assert "x.zip" in html
    assert "y.img" not in html
    assert "3 items" in html
    assert 'title="2026-01-01 00:00 UTC"' in html
    assert " ago</div>" in html
    assert fake.listings == 1


def test_pagination(client):
    _, html = get(client, "/many/?page=3")
    assert "2500 items" in html
    assert "page 3 of 9" in html
    assert "f0600.zip" in html
    assert "f0599.zip" not in html


def test_pagination_clamps_out_of_range(client):
    _, html = get(client, "/many/?page=99")
    assert "page 9 of 9" in html
    assert "f2499.zip" in html


def test_search_is_recursive_and_single_pass(client, fake):
    _, html = get(client, "/search/buckets/onelots-builds-bucket/a/?search=SUB")
    assert fake.listings == 1
    assert "sub/y.img" in html
    assert "sub/deep/z.json" in html
    assert "x.zip" not in html
    assert "Other/" not in html


def test_search_ignores_current_path(client):
    _, html = get(client, "/search/buckets/onelots-builds-bucket/a/?search=a")
    assert "x.zip" not in html


def test_browse_redirects_search_escaped(client):
    r, _ = get(client, "/buckets/onelots-builds-bucket/a/?search=x%26y")
    assert r.status_code == 302
    assert "search=x%26y" in r.headers["Location"]


def test_listings_are_cached(client, fake):
    get(client, "/a/")
    get(client, "/a/?page=2")
    assert fake.listings == 1


def test_zips_come_first(client):
    _, html = get(client, "/b/")
    assert html.index("rom.zip") < html.index("boot.img")


def test_folder_order():
    names = ["b/", "17/", "24.0/", "A/", "20261027/", "20261111/", "16/", "9.1/", "24.1/"]
    assert sorted(names, key=s3.folder_order) == [
        "20261111/",
        "20261027/",
        "24.1/",
        "24.0/",
        "17/",
        "16/",
        "9.1/",
        "A/",
        "b/",
    ]


def test_list_builds(fake):
    builds = s3.list_builds(fake, "bucket", "rom")
    assert [(b.device, b.date) for b in builds] == [("dev1", "20261111"), ("dev1", "20261105"), ("dev2", "20260901")]
    latest = builds[0]
    assert (latest.android, latest.version, latest.day) == ("17", "24.0", "2026-11-11")
    assert latest.zip.name == "rom/dev1/17/24.0/20261111/rom-20261111-dev1.zip"
    assert [f.name.rsplit("/", 1)[-1] for f in latest.files] == ["rom-20261111-dev1.zip", "boot.img"]
    assert builds[2].zip is None


def test_s3_errors(client):
    r, html = get(client, "/buckets/missing")
    assert r.status_code == 404
    assert "does not exist" in html


def test_json_is_served_directly(client):
    r, _ = get(client, "/download/buckets/b/a/sub/deep/z.json")
    assert r.status_code == 200
    assert r.mimetype == "application/json"
    r, _ = get(client, "/download/buckets/b/missing.json")
    assert r.status_code == 404


def test_other_files_redirect_to_s3(client):
    r, _ = get(client, "/download/buckets/b/a/x.zip")
    assert r.status_code == 302
    assert r.headers["Location"] == "https://s3.example/presigned"


def test_static_files_are_versioned_and_cached(client):
    _, html = get(client, "/")
    assert "/static/style.css?v=" in html
    r, _ = get(client, "/static/style.css")
    assert r.headers["Cache-Control"] == "public, max-age=31536000"
