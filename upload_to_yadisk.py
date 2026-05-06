#!/usr/bin/env python3
import argparse
import json
import pathlib
import sys
import time
import urllib.parse

import requests

API = "https://cloud-api.yandex.net/v1/disk/resources"


def req(method, url, token, **kwargs):
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"OAuth {token}"
    r = requests.request(method, url, headers=headers, timeout=120, **kwargs)
    if r.status_code >= 400:
        print(f"HTTP {r.status_code} {r.reason}\n{r.text}", file=sys.stderr)
        r.raise_for_status()
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True, help="Yandex OAuth token")
    ap.add_argument("--file", default="/root/.openclaw/workspace/olcrtc/build/olcrtc-windows-amd64.zip")
    ap.add_argument("--path", default="/olcrtc-windows-amd64.zip", help="Yandex Disk path, e.g. /olcrtc.zip")
    ap.add_argument("--publish", action="store_true", help="Publish file and print public_url")
    args = ap.parse_args()

    f = pathlib.Path(args.file)
    if not f.exists():
        raise SystemExit(f"File not found: {f}")

    # 1) get upload URL
    params = {"path": args.path, "overwrite": "true"}
    r = req("GET", API + "/upload", args.token, params=params)
    link = r.json()
    href = link["href"]
    method = link.get("method", "PUT")
    print(f"Upload URL acquired; uploading {f.name} ({f.stat().st_size} bytes) -> disk:{args.path}")

    # 2) upload body to href; OAuth is not needed for uploader URL
    with f.open("rb") as fh:
        ur = requests.request(method, href, data=fh, timeout=600)
    if ur.status_code not in (201, 202):
        print(f"Upload failed HTTP {ur.status_code}: {ur.text}", file=sys.stderr)
        ur.raise_for_status()
    print(f"Uploaded: HTTP {ur.status_code}")

    if args.publish:
        # Sometimes metadata move is async after 202, retry publish a bit.
        pub_url = API + "/publish"
        last = None
        for _ in range(10):
            pr = requests.put(pub_url, headers={"Authorization": f"OAuth {args.token}"}, params={"path": args.path}, timeout=120)
            if pr.status_code < 400:
                break
            last = pr
            time.sleep(2)
        else:
            print(f"Publish failed HTTP {last.status_code}: {last.text}", file=sys.stderr)
            last.raise_for_status()

        # Fetch metadata with public_url
        mr = req("GET", API, args.token, params={"path": args.path, "fields": "public_url,name,size,sha256,md5"})
        meta = mr.json()
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        if meta.get("public_url"):
            print("PUBLIC_URL=" + meta["public_url"])


if __name__ == "__main__":
    main()
