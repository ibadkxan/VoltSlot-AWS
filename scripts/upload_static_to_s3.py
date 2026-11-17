"""Upload VoltSlot's public static assets to an S3 bucket.

Usage:
  AWS_PROFILE=your-profile python scripts/upload_static_to_s3.py --bucket YOUR_BUCKET

The caller's AWS identity needs s3:PutObject and s3:ListBucket permissions.
"""
import argparse
import mimetypes
from pathlib import Path
import boto3

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "static"


def main():
    parser = argparse.ArgumentParser(description="Upload VoltSlot static assets to S3")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", default="ap-south-1")
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name=args.region)
    files = [p for p in STATIC_DIR.rglob("*") if p.is_file()]
    if not files:
        raise SystemExit("No files found in static/.")

    for path in files:
        key = "static/" + path.relative_to(STATIC_DIR).as_posix()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        s3.upload_file(
            str(path), args.bucket, key,
            ExtraArgs={"ContentType": content_type, "CacheControl": "public, max-age=86400"},
        )
        print(f"Uploaded s3://{args.bucket}/{key}")

    print("\nSet ASSET_BASE_URL to:")
    print(f"https://{args.bucket}.s3.{args.region}.amazonaws.com/static")


if __name__ == "__main__":
    main()
