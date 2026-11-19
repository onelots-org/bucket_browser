import os
from typing import ClassVar


class Config:  # noqa: D101
    DEBUG = os.getenv("DEBUG", "").lower() in ("1", "true")

    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
    AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", "eu-central-1")
    AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL", None)

    AWS_KWARGS: ClassVar[dict[str, str | None]] = {
        "aws_access_key_id": AWS_ACCESS_KEY_ID,
        "aws_secret_access_key": AWS_SECRET_ACCESS_KEY,
        "region_name": AWS_DEFAULT_REGION,
    }

    if AWS_ENDPOINT_URL:
        AWS_KWARGS["endpoint_url"] = AWS_ENDPOINT_URL

    SITE_URL = os.getenv("SITE_URL", "https://downloads.onelots.org")

    SEND_FILE_MAX_AGE_DEFAULT = 365 * 24 * 3600

    PAGE_ITEMS = int(os.getenv("PAGE_ITEMS", "300"))
