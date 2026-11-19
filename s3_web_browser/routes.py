import zlib
from pathlib import Path

import boto3
import botocore
from flask import Flask, Response, redirect, render_template, request, url_for

from s3_web_browser.s3 import list_entries

ROOT_BUCKET = "onelots-builds-bucket"

ERRORS = {
    "AccessDenied": (403, "You do not have permission to access this bucket."),
    "NoSuchBucket": (404, "The specified bucket does not exist."),
    "NoSuchKey": (404, "The specified file does not exist."),
}


def register_routes(app: Flask) -> None:  # noqa:C901
    s3_client = boto3.client("s3", **app.config["AWS_KWARGS"])
    page_items = app.config["PAGE_ITEMS"]
    static = Path(app.static_folder)
    asset_ver = f"{zlib.crc32(b''.join(f.read_bytes() for f in sorted(static.iterdir()))):08x}"

    @app.context_processor
    def inject_globals() -> dict:
        return {"ROOT_BUCKET": ROOT_BUCKET, "ASSET_VER": asset_ver}

    @app.errorhandler(botocore.exceptions.ClientError)
    def s3_error(e: botocore.exceptions.ClientError) -> tuple[str, int]:
        code = e.response["Error"]["Code"]
        status, msg = ERRORS.get(code, (502, f"An unknown error occurred: {code}"))
        return render_template("error.html", error=msg), status

    def render_listing(bucket_name: str, path: str, search: str = "") -> str:
        contents = list_entries(s3_client, bucket_name, path, search)
        total_pages = max(1, -(-len(contents) // page_items))
        page = max(1, min(request.args.get("page", 1, type=int), total_pages))
        return render_template(
            "bucket_contents.html",
            contents=contents[(page - 1) * page_items : page * page_items],
            total_items=len(contents),
            bucket_name=bucket_name,
            path=path,
            search_param=search,
            current_page=page,
            total_pages=total_pages,
        )

    @app.route("/", methods=["GET"])
    def root_index() -> str:
        response = s3_client.list_objects_v2(
            Bucket=ROOT_BUCKET,
            Delimiter="/",
        )
        prefixes = response.get("CommonPrefixes", [])
        virtual_buckets = [
            {"name": p["Prefix"].rstrip("/"), "count": len(list_entries(s3_client, ROOT_BUCKET, p["Prefix"]))}
            for p in prefixes
        ]
        return render_template("index.html", buckets=virtual_buckets)

    @app.route("/search/buckets/<bucket_name>", defaults={"path": ""})
    @app.route("/search/buckets/<bucket_name>/<path:path>")
    def search_bucket(bucket_name: str, path: str) -> str:
        return render_listing(bucket_name, path, request.args.get("search", ""))

    @app.route("/buckets/<bucket_name>", defaults={"path": ""})
    @app.route("/buckets/<bucket_name>/<path:path>")
    def view_bucket(bucket_name: str, path: str) -> str | Response:
        if search := request.args.get("search", ""):
            return redirect(url_for("search_bucket", bucket_name=bucket_name, path=path, search=search))
        return render_listing(bucket_name, path)

    @app.route("/download/buckets/<bucket_name>/<path:path>")
    def download_file(bucket_name: str, path: str) -> Response:
        # OTA clients (LineageOS Updater) don't follow redirects for the update list
        if path.endswith(".json"):
            try:
                obj = s3_client.get_object(Bucket=bucket_name, Key=path)
            except botocore.exceptions.ClientError as e:
                if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                    return Response("Not found\n", status=404, mimetype="text/plain")
                raise
            return Response(obj["Body"].read(), mimetype="application/json", headers={"Cache-Control": "no-cache"})
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket_name, "Key": path},
            ExpiresIn=3600,
        )
        return redirect(url)

    @app.route("/<path:path>")
    def index_path(path: str) -> str | Response:
        return view_bucket(ROOT_BUCKET, path)
