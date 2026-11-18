#!/usr/bin/env python3
"""Fetch Goodix touch-panel packages from the Microsoft Update Catalog.

Usage:
    fetch-catalog-goodix.py [QUERY ...]

Prints every update id and title, then downloads each payload into
/tmp/goodix-pkgs.  The catalog needs a browser user agent, its search results
are keyed by GUID, and the download link only appears after a form POST to
DownloadDialog.aspx - all three are handled here.

Why this exists: the catalog carries Goodix *touch panel* update packages that
contain a firmware image (tpfw.bin), a per-panel config and a UMDF filter driver
which flashes them.  One of those packages matches this panel's HID identity
(HID\\VID_27C6&UP:FF00_U:0001) - see docs/FIRMWARE-PACKAGES.md.
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request

UA = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
}
FORM = dict(UA, **{"Content-Type": "application/x-www-form-urlencoded"})
BASE = "https://www.catalog.update.microsoft.com/"
OUT = "/tmp/goodix-pkgs"
DEFAULT_QUERIES = ("Goodix HIDClass",)


def get(url, data=None, headers=UA):
    req = urllib.request.Request(url, data=data, headers=headers)
    return urllib.request.urlopen(req, timeout=90).read()


def search(query):
    """Return [(guid, title)] for one catalog search."""
    page = get(BASE + "Search.aspx?q=" + urllib.parse.quote(query)).decode(
        "utf-8", "replace")
    found = []
    for chunk in page.split("<tr"):
        if 'goToDetails("' not in chunk:
            continue
        guid = re.search(r'goToDetails\("([0-9a-fA-F-]{36})"\)', chunk)
        if not guid:
            continue
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", chunk)).strip()
        text = re.sub(r'^id="[^"]*"[^>]*>', "", text).strip()
        found.append((guid.group(1), text[:90]))
    return list(dict.fromkeys(found))


def download_url(guid):
    payload = "updateIDs=" + urllib.parse.quote(json.dumps(
        [{"size": 0, "languages": "", "uidInfo": guid, "updateID": guid}]))
    dialog = get(BASE + "DownloadDialog.aspx", payload.encode(),
                 FORM).decode("utf-8", "replace")
    urls = re.findall(r"https://[^'\"\s]+\.(?:cab|msu|exe|zip)", dialog)
    return list(dict.fromkeys(urls))


def main():
    queries = sys.argv[1:] or list(DEFAULT_QUERIES)
    os.makedirs(OUT, exist_ok=True)
    for query in queries:
        updates = search(query)
        print("== %s -> %d updates" % (query, len(updates)))
        for guid, title in updates:
            print("   %s  %s" % (guid, title))
        for guid, _title in updates:
            for url in download_url(guid):
                name = os.path.join(OUT, url.rsplit("/", 1)[-1])
                if os.path.exists(name):
                    continue
                blob = get(url)
                with open(name, "wb") as fh:
                    fh.write(blob)
                print("   saved %s (%d bytes)" % (os.path.basename(name),
                                                  len(blob)))
    print("payloads in", OUT, "- extract with: 7z x -o<dir> <file>.cab")


if __name__ == "__main__":
    main()
