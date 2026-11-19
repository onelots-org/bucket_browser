import boto3
import botocore
from flask import Flask, Response, redirect, render_template, request

from s3_web_browser.s3 import list_objects, parse_responses

ROOT_BUCKET = "onelots-builds-bucket"

ERRORS = {
    "AccessDenied": (403, "You do not have permission to access this bucket."),
    "NoSuchBucket": (404, "The specified bucket does not exist."),
    "NoSuchKey": (404, "The specified file does not exist."),
}


def register_routes(app: Flask) -> None:  # noqa:C901
    s3_client = boto3.client("s3", **app.config["AWS_KWARGS"])

    @app.context_processor
    def inject_globals() -> dict:
        return {"ROOT_BUCKET": ROOT_BUCKET}

    @app.errorhandler(botocore.exceptions.ClientError)
    def s3_error(e: botocore.exceptions.ClientError) -> tuple[str, int]:
        code = e.response["Error"]["Code"]
        status, msg = ERRORS.get(code, (502, f"An unknown error occurred: {code}"))
        return render_template("error.html", error=msg), status

    @app.route("/", methods=["GET"])
    def root_index() -> str:
        response = s3_client.list_objects_v2(
            Bucket=ROOT_BUCKET,
            Delimiter="/",
        )
        prefixes = response.get("CommonPrefixes", [])
        virtual_buckets = [
            {"name": p["Prefix"].rstrip("/")} for p in prefixes
        ]
        return render_template("index.html", buckets=virtual_buckets)

    @app.route("/search/buckets/<bucket_name>", defaults={"path": ""})
    @app.route("/search/buckets/<bucket_name>/<path:path>")
    def search_bucket(bucket_name: str, path: str) -> str:
        page = request.args.get("page", 1, type=int)
        items_per_page = app.config["PAGE_ITEMS"]
        paginator = s3_client.get_paginator("list_objects_v2")
        all_entries = []
        all_prefixes = []

        # Collect all objects — extend (not assign) to keep results across all S3 pages
        for page_iterator in paginator.paginate(Bucket=bucket_name, Prefix=path):
            if "Contents" in page_iterator:
                all_entries.extend(
                    {"Key": item["Key"], "Size": item["Size"], "LastModified": item["LastModified"]}
                    for item in page_iterator["Contents"]
                    if not item["Key"].endswith("/")
                )

        for page_iterator in paginator.paginate(Bucket=bucket_name, Prefix=path, Delimiter="/"):
            if "CommonPrefixes" in page_iterator:
                all_prefixes.extend(page_iterator["CommonPrefixes"])

        # Create response structure
        response = {"Contents": all_entries, "CommonPrefixes": all_prefixes}

        search_param = request.args.get("search", "")
        contents = parse_responses([response], search_param)

        # Calculate pagination
        total_items = len(contents)
        total_pages = (total_items + items_per_page - 1) // items_per_page if total_items else 1
        # Clamp page to valid range
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * items_per_page
        end_idx = start_idx + items_per_page
        paginated_contents = contents[start_idx:end_idx]

        return render_template(
            "bucket_contents.html",
            contents=paginated_contents,
            bucket_name=bucket_name,
            path=path,
            search_param=search_param,
            current_page=page,
            total_pages=total_pages,
        )

    @app.route("/buckets/<bucket_name>", defaults={"path": ""})
    @app.route("/buckets/<bucket_name>/<path:path>")
    def view_bucket(bucket_name: str, path: str) -> str:
        # If a search param is present on a browse URL, redirect to the search route
        search_param = request.args.get("search", "")
        if search_param:
            return redirect(
                request.url_root.rstrip("/")
                + f"/search/buckets/{bucket_name}/{path}".rstrip("/")
                + f"?search={search_param}"
            )

        page = request.args.get("page", 1, type=int)
        items_per_page = app.config["PAGE_ITEMS"]

        # Count total objects and find the continuation token for the requested page in one pass
        paginator = s3_client.get_paginator("list_objects_v2")
        total_objects = 0
        continuation_token = None
        for pages_seen, page_iterator in enumerate(
            paginator.paginate(
                Bucket=bucket_name,
                Prefix=path,
                Delimiter="/",
                PaginationConfig={"PageSize": items_per_page},
            ),
            start=1,
        ):
            if "CommonPrefixes" in page_iterator:
                total_objects += len(page_iterator["CommonPrefixes"])
            if "Contents" in page_iterator:
                total_objects += sum(1 for obj in page_iterator["Contents"] if not obj["Key"].endswith("/"))

            # Capture the token needed to reach the requested page
            if pages_seen == page - 1:
                continuation_token = page_iterator.get("NextContinuationToken")

        total_pages = (total_objects + items_per_page - 1) // items_per_page if total_objects else 1
        # Clamp page to valid range
        page = max(1, min(page, total_pages))

        # Re-fetch if clamping changed the page (avoids re-running the full count)
        response = list_objects(s3_client, bucket_name, path, items_per_page, "/", continuation_token)
        contents = parse_responses([response], "")

        return render_template(
            "bucket_contents.html",
            contents=contents,
            bucket_name=bucket_name,
            path=path,
            search_param="",
            current_page=page,
            total_pages=total_pages,
        )

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
            return Response(
                obj["Body"].read(),
                mimetype="application/json",
                headers={"Cache-Control": "no-cache"},
            )
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket_name, "Key": path},
            ExpiresIn=3600,
        )  # URL expires in 1 hour
        return redirect(url)

    @app.route("/<path:path>")
    def index_path(path: str) -> str:
        return view_bucket(ROOT_BUCKET, path)
