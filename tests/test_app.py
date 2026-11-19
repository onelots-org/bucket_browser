import datetime as dt
import io
from unittest import mock

import botocore.exceptions
import pytest

from s3_web_browser import create_app, downloads, s3

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
    "rom/dev3/17/24.0/20261101/rom-20261101-dev3.zip",
    "rom/OTA_UPDATES/17/dev1.json",
    "rom/dev1/17/24.0/notes.txt",
    *(f"many/f{i:04}.zip" for i in range(2500)),
]
DATE = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
UPLOADED = {"rom/dev3/17/24.0/20261101/rom-20261101-dev3.zip": dt.datetime(2026, 1, 2, tzinfo=dt.UTC)}


def client_error(code):
    return botocore.exceptions.ClientError({"Error": {"Code": code}}, "op")


class FakeS3:
    def __init__(self):
        self.listings = 0

    def get_paginator(self, _):
        return self

    def paginate(self, Bucket, Prefix="", Delimiter=None):
        self.listings += 1
        if Prefix.startswith("denied/"):
            raise client_error("AccessDenied")
        prefixes, contents = set(), []
        for key in (k for k in KEYS if k.startswith(Prefix)):
            rel = key[len(Prefix) :]
            if Delimiter and "/" in rel:
                prefixes.add(Prefix + rel.split("/", 1)[0] + "/")
            else:
                contents.append({"Key": key, "Size": 1234, "LastModified": UPLOADED.get(key, DATE)})
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
def app(fake, tmp_path):
    with mock.patch("boto3.client", return_value=fake):
        app = create_app()
    app.config["DB_PATH"] = str(tmp_path / "downloads.sqlite3")
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def get(client, url):
    r = client.get(url)
    return r, r.get_data(as_text=True)


def test_index(client):
    r, html = get(client, "/")
    assert r.status_code == 200
    assert "many" in html
    assert "2500 items" in html
    assert 'src="/static/rom-' not in html
    assert 'href="/roms/rom/"' in html
    assert "3 devices" in html
    assert html.index("rom-20261101-dev3.zip") < html.index("rom-20261111-dev1.zip")
    assert 'href="/buckets/onelots-builds-bucket/many/"' in html
    assert "Latest releases" in html
    assert 'href="/roms/rom/dev1/" class="release-name"' in html
    assert "rom-20261111-dev1.zip" in html
    assert "rom-20261105-dev1.zip" not in html


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


def test_rom_names(client):
    with mock.patch.dict("s3_web_browser.routes.ROM_NAMES", {"rom": "Some ROM"}):
        _, html = get(client, "/roms/rom/")
    assert "Some ROM" in html


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
    assert [(b.device, b.date) for b in builds] == [
        ("dev1", "20261111"),
        ("dev1", "20261105"),
        ("dev3", "20261101"),
        ("dev2", "20260901"),
    ]
    latest = builds[0]
    assert (latest.android, latest.version, latest.day) == ("17", "24.0", "2026-11-11")
    assert latest.zip.name == "rom/dev1/17/24.0/20261111/rom-20261111-dev1.zip"
    assert [f.name.rsplit("/", 1)[-1] for f in latest.files] == ["rom-20261111-dev1.zip", "boot.img"]
    assert builds[3].zip is None


def test_rom_devices(client):
    r, html = get(client, "/roms/rom/")
    assert r.status_code == 200
    assert html.index("dev1") < html.index("dev2")
    assert "2 builds" in html
    assert "1 build<" in html
    assert "rom-20261111-dev1.zip" in html
    assert "rom-20261105-dev1.zip" not in html


def test_device_builds(client):
    r, html = get(client, "/roms/rom/dev1/")
    assert r.status_code == 200
    assert html.index("2026-11-11") < html.index("2026-11-05")
    assert html.count('class="badge"') == 1
    assert "boot.img" in html


def test_latest_build(client):
    r, _ = get(client, "/roms/rom/dev1/latest")
    assert r.status_code == 302
    assert r.headers["Location"].endswith(
        "/download/buckets/onelots-builds-bucket/rom/dev1/17/24.0/20261111/rom-20261111-dev1.zip"
    )
    assert get(client, "/roms/rom/dev2/latest")[0].status_code == 404
    _, html = get(client, "/roms/rom/dev1/")
    assert 'href="/roms/rom/dev1/latest"' in html


def test_device_without_builds_falls_back_to_folders(client):
    r, _ = get(client, "/roms/rom/nope/")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/buckets/onelots-builds-bucket/rom/nope/")


def test_breadcrumb_links_to_catalog(client):
    _, html = get(client, "/rom/dev1/17/24.0/")
    assert 'href="/roms/rom/"' in html
    assert 'href="/roms/rom/dev1/"' in html
    assert 'href="/buckets/onelots-builds-bucket/rom/dev1/17/"' in html


def test_embeds(client):
    _, html = get(client, "/roms/rom/dev1/")
    assert '<meta property="og:title" content="dev1 · rom — Onelots">' in html
    assert "Latest rom build for dev1: 24.0 (Android 17) from 2026-11-11" in html
    assert '<meta property="og:url" content="https://downloads.onelots.org/roms/rom/dev1/">' in html
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    assert "https://downloads.onelots.org/static/og.jpg?v=" in html


def test_unknown_folder(client):
    r, html = get(client, "/nope/")
    assert r.status_code == 404
    assert "does not exist" in html
    r, html = get(client, "/search/buckets/onelots-builds-bucket/a/?search=nothing")
    assert r.status_code == 200
    assert "No results" in html


def test_rom_without_builds_falls_back_to_folders(client):
    r, _ = get(client, "/roms/a/")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/buckets/onelots-builds-bucket/a/")


def test_s3_errors(client):
    r, html = get(client, "/denied/")
    assert r.status_code == 403
    assert "permission" in html


def test_other_buckets_are_refused(client, fake):
    for url in (
        "/buckets/other",
        "/buckets/other/a/",
        "/search/buckets/other?search=a",
        "/download/buckets/other/a/x.zip",
    ):
        r, _ = get(client, url)
        assert r.status_code == 404
    assert fake.listings == 0


def test_json_is_served_directly(client):
    r, _ = get(client, "/download/buckets/onelots-builds-bucket/a/sub/deep/z.json")
    assert r.status_code == 200
    assert r.mimetype == "application/json"
    r, _ = get(client, "/download/buckets/onelots-builds-bucket/missing.json")
    assert r.status_code == 404


def test_downloads_are_counted(app, client):
    url = "/download/buckets/onelots-builds-bucket/a/x.zip"
    client.get(url)
    client.get(url)
    client.head(url)
    client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Discordbot/2.0)"})
    client.get("/download/buckets/onelots-builds-bucket/a/sub/deep/z.json")
    assert downloads.download_counts(app.config["DB_PATH"]) == {"a/x.zip": 2}


def test_download_survives_a_broken_counter(app, client):
    app.config["DB_PATH"] = "/dev/null/downloads.sqlite3"
    r, _ = get(client, "/download/buckets/onelots-builds-bucket/a/x.zip")
    assert r.status_code == 302


def test_other_files_redirect_to_s3(client):
    r, _ = get(client, "/download/buckets/onelots-builds-bucket/a/x.zip")
    assert r.status_code == 302
    assert r.headers["Location"] == "https://s3.example/presigned"


def test_static_files_are_versioned_and_cached(client):
    _, html = get(client, "/")
    assert "/static/style.css?v=" in html
    r, _ = get(client, "/static/style.css")
    assert r.headers["Cache-Control"] == "public, max-age=31536000"
