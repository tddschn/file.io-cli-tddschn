# Copyright (c) 2018 Niklas Rosenstein
# Copyright (c) 2023 Teddy Xinyuan Chen
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.

from __future__ import division, print_function

import asyncio
import argparse
import base64
from functools import cache
from io import BufferedReader
from typing import BinaryIO, Literal
import json
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from file_io_cli_tddschn import __version__, __author__
from file_io_cli_tddschn.utils import get_abs_path


class MultipartFileEncoder(object):
    def __init__(self, field, fp, filename=None, boundary=None, headers=None):
        self.field = field
        self.fp = fp
        self.filename = filename
        self.boundary = (boundary or uuid.uuid4().hex).encode("ascii")
        self.content_type = b"multipart/form-data; boundary=" + self.boundary

        headers = dict(headers or {})

        if "Content-Disposition" not in headers:
            disposition = 'form-data; name="{}"'.format(self.field)
            if self.filename:
                disposition += '; filename="{}"'.format(self.filename)
            headers["Content-Disposition"] = disposition

        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/octet-stream"

        self.headers = b"\r\n".join(
            "{}: {}".format(k, v).encode() for k, v in headers.items()
        )

    def compute_size(self, include_final_boundary=True):
        pos = self.fp.tell()
        self.fp.seek(0, os.SEEK_END)
        size = self.fp.tell()
        self.fp.seek(pos)
        size += len(self.boundary) + 4 + 4 + len(self.headers) + 2
        if include_final_boundary:
            size += 6 + len(self.boundary)
        return size

    def iter_encode(self, include_final_boundary=True, chunksize=8096):
        yield b"--"
        yield self.boundary
        yield b"\r\n"

        yield self.headers
        yield b"\r\n"
        yield b"\r\n"

        # TODO: Check if boundary value occurs in data body.
        while True:
            data = self.fp.read(chunksize)
            if not data:
                break
            yield data

        yield b"\r\n"

        if include_final_boundary:
            yield b"--"
            yield self.boundary
            yield b"--\r\n"


class GeneratorFileReader(object):
    def __init__(self, gen):
        self.gen = gen
        self.buffer = b""

    def readable(self):
        return True

    def read(self, n=None):
        if n is None:
            res = self.buffer + b"".join(self.gen)
            self.buffer = b""
            return res
        elif n <= 0:
            return b""
        else:
            res = b""
            while n > 0:
                part = self.buffer[:n]
                res += part
                self.buffer = self.buffer[n:]
                n -= len(part)
                assert n >= 0
                if not self.buffer:
                    try:
                        self.buffer = next(self.gen)
                    except StopIteration:
                        break
                else:
                    break
            return res


class FileMonitor(object):
    def __init__(self, fp, callback=None):
        self.fp = fp
        self.bytes_read = 0
        self.callback = callback

    def __getattr__(self, key):
        return getattr(self.fp, key)

    def read(self, n):
        res = self.fp.read(n)
        self.bytes_read += len(res)
        if self.callback:
            self.callback(self)
        return res


class ProgressDisplay(object):
    SPINCHARS = "\\|/-"

    def __init__(self, n_max=None):
        self.n_max = n_max
        self.alteration = 0
        self.last_print = None

    def update(self, n_read, force=False):
        if (
            not force
            and self.last_print is not None
            and time.perf_counter() - self.last_print < 0.25
        ):
            return
        self.last_print = time.perf_counter()
        self.__clear_line(file=sys.stderr)
        if self.n_max is None:
            c = self.SPINCHARS[self.alteration % len(self.SPINCHARS)]
            print(
                "\r{} ({})".format(c, self.human_size(n_read)), end="", file=sys.stderr
            )
        else:
            w = 60
            p = n_read / self.n_max
            l = int(w * p)

            bar = "[" + "=" * l + " " * (w - l) + "]"
            print(
                "\r{} {}% ({} / {})".format(
                    bar,
                    int(p * 100),
                    self.human_size(n_read),
                    self.human_size(self.n_max),
                ),
                end="",
                file=sys.stderr,
            )
        sys.stderr.flush()
        self.alteration += 1

    def finish(self):
        print(file=sys.stderr)

    @staticmethod
    def __clear_line(file=None):
        print("\r\33[K", end="", file=file)

    @staticmethod
    def human_size(n_bytes, units=[" bytes", "KB", "MB", "GB", "TB", "PB", "EB"]):
        # https://stackoverflow.com/a/43750422/791713
        return (
            str(n_bytes) + units[0]
            if n_bytes < 1024
            else ProgressDisplay.human_size(n_bytes >> 10, units[1:])
        )


def stream_file(fp, chunksize=8192):
    while True:
        data = fp.read(chunksize)
        if data:
            yield data
        else:
            break


def spawn_process(*args, **kwargs):
    on_exit = kwargs.pop("on_exit", None)

    def worker():
        subprocess.call(*args, **kwargs)
        if on_exit is not None:
            on_exit()

    threading.Thread(target=worker).start()


def get_args(prog=None) -> argparse.Namespace | Literal[0]:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Upload a file to file.io and print the download link. Supports stdin.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "-e",
        "--expires",
        metavar="E",
        help="set the expiration time for the uploaded file",
    )
    parser.add_argument("-n", "--name", help="specify or override the filename")
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="hide the progress bar"
    )
    parser.add_argument(
        "-c", "--clip", action="store_true", help="copy the URL to your clipboard"
    )
    parser.add_argument(
        "-t",
        "--tar",
        metavar="PATH",
        type=get_abs_path,
        help="create a TAR archive from the specified file or directory",
    )
    parser.add_argument(
        "-z",
        "--gzip",
        action="store_true",
        help="filter the TAR archive through gzip (only with -t, --tar)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="print the server response"
    )
    parser.add_argument(
        "-N", "--upload-times", type=int, help="upload the file N times", default=1
    )
    parser.add_argument("file", nargs="?", type=get_abs_path, help="the file to upload")
    args = parser.parse_args()

    if not args.file and not args.tar and sys.stdin.isatty():
        parser.print_usage()
        return 0
    if args.file and args.tar:
        parser.error("conflicting options: file and -t, --tar")
    return args


def _format_unexpected_response_error(response, reason="non-JSON response"):
    content_type = response.headers.get("Content-Type", "").strip()
    response_url = getattr(response, "url", "")

    body_preview = ""
    try:
        body_preview = response.text.strip().replace("\r", " ").replace("\n", " ")
    except Exception:
        body_preview = ""
    if len(body_preview) > 200:
        body_preview = body_preview[:197] + "..."

    parts = [f"Upload API returned {reason} (HTTP {response.status_code})"]
    if response_url:
        parts.append(f"url={response_url}")
    if content_type:
        parts.append(f"content-type={content_type}")
    if response.history:
        redirect_chain = " -> ".join(r.url for r in response.history + [response])
        parts.append(f"redirect-chain={redirect_chain}")
    if body_preview:
        parts.append(f"body-preview={body_preview!r}")
    return "; ".join(parts)


def _parse_upload_response_json(response):
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(_format_unexpected_response_error(response)) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            _format_unexpected_response_error(
                response, reason="unexpected JSON payload"
            )
        )
    return payload


def _extract_link_from_payload(payload):
    link = payload.get("link")
    if isinstance(link, str) and link:
        return link

    payload_preview = json.dumps(payload, ensure_ascii=False)
    if len(payload_preview) > 200:
        payload_preview = payload_preview[:197] + "..."
    raise RuntimeError(
        "Upload API JSON response did not include a valid 'link' field: "
        + payload_preview
    )


LIMEWIRE_API_BASE_URL = "https://api.limewire.com"
LIMEWIRE_LINK_BASE_URL = "https://limewire.com/"
FILE_IO_ORIGIN = "https://www.file.io"
LIMEWIRE_ORIGIN = "https://limewire.com"

_EXPIRES_RE = re.compile(r"^(\d+)([smhdw])$")


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _parse_expires_to_iso(expires: str | None) -> str:
    now = datetime.now(timezone.utc)
    if not expires:
        dt = now + timedelta(days=7)
        return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    match = _EXPIRES_RE.match(expires.strip())
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        delta = {
            "s": timedelta(seconds=value),
            "m": timedelta(minutes=value),
            "h": timedelta(hours=value),
            "d": timedelta(days=value),
            "w": timedelta(weeks=value),
        }[unit]
        dt = now + delta
        return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    raw = expires.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise RuntimeError(
            "Invalid expires value. Use relative durations like 1d, 1w, 12h "
            "or an ISO-8601 timestamp."
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (
        dt.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _random_b64(n_bytes: int) -> str:
    return base64.b64encode(os.urandom(n_bytes)).decode("ascii")


def _sha1_file(file_path: str) -> str:
    digest = hashlib.sha1()
    with open(file_path, "rb") as fp:
        while True:
            chunk = fp.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _materialize_upload_input(args) -> tuple[str, int, callable]:
    if args.file:
        path = args.file
        return path, os.stat(path).st_size, lambda: None

    suffix = ".tgz" if args.gzip else ".tar" if args.tar else ".bin"
    tmp = tempfile.NamedTemporaryFile(
        prefix="fileio-upload-", suffix=suffix, delete=False
    )
    tmp_path = tmp.name
    tmp.close()

    def cleanup() -> None:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    try:
        if args.tar:
            flags = "-czf-" if args.gzip else "-cf-"
            proc = subprocess.Popen(
                ["tar", flags, args.tar],
                stdout=subprocess.PIPE,
            )
            assert proc.stdout is not None
            with open(tmp_path, "wb") as out:
                while True:
                    chunk = proc.stdout.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            return_code = proc.wait()
            if return_code != 0:
                raise RuntimeError(f"tar process failed with exit code {return_code}")
        else:
            src = sys.stdin if sys.version_info[0] == 2 else sys.stdin.buffer
            with open(tmp_path, "wb") as out:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
    except Exception:
        cleanup()
        raise

    return tmp_path, os.stat(tmp_path).st_size, cleanup


def _raise_for_limewire_error(response, context: str) -> None:
    try:
        response.raise_for_status()
        return
    except Exception as exc:
        text = response.text.strip().replace("\n", " ").replace("\r", " ")
        if len(text) > 300:
            text = text[:297] + "..."
        raise RuntimeError(
            f"{context} failed (HTTP {response.status_code}): {text or '<empty body>'}"
        ) from exc


def _limewire_upload_once(
    file_path: str,
    file_size: int,
    file_name: str,
    expires_at: str,
    quiet: bool,
):
    import requests

    session = requests.Session()
    base_headers = {"Origin": FILE_IO_ORIGIN, "Referer": FILE_IO_ORIGIN + "/"}

    crossdomain_res = session.get(
        f"{LIMEWIRE_API_BASE_URL}/crossdomain",
        headers=base_headers,
        timeout=30,
    )
    _raise_for_limewire_error(crossdomain_res, "crossdomain bootstrap")
    claim_token = crossdomain_res.json().get("claimToken")
    if not isinstance(claim_token, str) or not claim_token:
        raise RuntimeError("crossdomain bootstrap response did not contain claimToken")

    claim_headers = {"x-claim-token": claim_token, **base_headers}

    user_key_res = session.post(
        f"{LIMEWIRE_API_BASE_URL}/sharing/user-encryption-key",
        headers=claim_headers,
        timeout=30,
    )
    _raise_for_limewire_error(user_key_res, "user encryption key bootstrap")
    user_key = user_key_res.json().get("userEncryptionKey")
    if not isinstance(user_key, dict):
        raise RuntimeError("user encryption key response missing userEncryptionKey")
    user_key_id = user_key.get("id")
    if not isinstance(user_key_id, str) or not user_key_id:
        raise RuntimeError("user encryption key response missing userEncryptionKey.id")

    bucket_id = str(uuid.uuid4())
    file_key_id = str(uuid.uuid4())
    content_item_id = str(uuid.uuid4())
    created_date = _utc_now_iso()

    create_upload_payload = {
        "sharingBucket": {
            "id": bucket_id,
            "sharingStatus": "SHARED",
            "sharingPermission": "VIEWER",
            "primaryEncryptionKeyId": file_key_id,
            "contentItemIds": [],
            "totalFileSize": file_size,
            "name": file_name,
            "createdDate": created_date,
            "deleted": False,
            "expiresAt": expires_at,
            "pinned": False,
        },
        "contentItem": {
            "id": content_item_id,
            "originalSharingBucketId": bucket_id,
            "s3Status": "PENDING",
            "itemType": "OTHER",
            "mediaType": "application/octet-stream",
            "size": file_size,
            "baseFileEncryptionKeyId": file_key_id,
            "ephemeralPublicKey": _random_b64(65),
            "previews": [],
            "createdDate": created_date,
            "deleted": False,
        },
        "fileEncryptionKey": {
            "id": file_key_id,
            "publicKey": _random_b64(65),
            "privateKeys": [
                {
                    "encryptedPrivateKey": _random_b64(48),
                    "encryptedByKeyType": "USER_ENCRYPTION_KEY",
                    "encryptedByKeyId": user_key_id,
                }
            ],
            "deleted": False,
            "createdDate": created_date,
        },
    }

    create_upload_res = session.post(
        f"{LIMEWIRE_API_BASE_URL}/sharing/upload/s3/multipart",
        headers={"Content-Type": "application/json", **claim_headers},
        data=json.dumps(create_upload_payload),
        timeout=30,
    )
    _raise_for_limewire_error(create_upload_res, "multipart upload creation")
    create_upload_data = create_upload_res.json()
    upload_id = create_upload_data.get("uploadId")
    object_key = create_upload_data.get("key")
    if not isinstance(upload_id, str) or not isinstance(object_key, str):
        raise RuntimeError("multipart upload creation response missing uploadId/key")

    upload_part_url_res = session.get(
        f"{LIMEWIRE_API_BASE_URL}/sharing/upload/s3/multipart/{upload_id}/1",
        params={"key": object_key},
        headers=claim_headers,
        timeout=30,
    )
    _raise_for_limewire_error(upload_part_url_res, "multipart part URL retrieval")
    upload_part_url_data = upload_part_url_res.json()
    signed_upload_method = upload_part_url_data.get("method")
    signed_upload_url = upload_part_url_data.get("url")
    if not isinstance(signed_upload_method, str) or not isinstance(
        signed_upload_url, str
    ):
        raise RuntimeError("multipart part URL response missing method/url")

    progress = ProgressDisplay(file_size) if not quiet else None
    with open(file_path, "rb") as fp:
        data_stream = fp
        if progress is not None:
            data_stream = FileMonitor(fp, lambda f: progress.update(f.bytes_read))

        upload_part_res = requests.request(
            signed_upload_method,
            signed_upload_url,
            data=data_stream,
            headers={"Content-Length": str(file_size)},
            timeout=120,
        )

    if progress is not None:
        progress.update(file_size, force=True)
        progress.finish()

    _raise_for_limewire_error(upload_part_res, "multipart part upload")
    etag = upload_part_res.headers.get("ETag")
    if not etag:
        raise RuntimeError("multipart part upload response missing ETag")

    complete_res = session.post(
        f"{LIMEWIRE_API_BASE_URL}/sharing/upload/s3/multipart/{upload_id}/complete",
        params={"key": object_key},
        headers={"Content-Type": "application/json", **claim_headers},
        data=json.dumps(
            {"parts": [{"ETag": etag, "PartNumber": 1, "Size": file_size}]}
        ),
        timeout=30,
    )
    _raise_for_limewire_error(complete_res, "multipart upload completion")

    finish_payload = {
        "sharingBucketId": bucket_id,
        "contentItem": {
            "id": content_item_id,
            "originalSharingBucketId": bucket_id,
            "s3Status": "UPLOADED",
            "itemType": "OTHER",
            "mediaType": "application/octet-stream",
            "size": file_size,
            "nameEncrypted": file_name,
            "sha1Encrypted": _sha1_file(file_path),
            "baseFileEncryptionKeyId": file_key_id,
            "ephemeralPublicKey": create_upload_payload["contentItem"][
                "ephemeralPublicKey"
            ],
            "metadata": {"metadataType": "unknown"},
            "previews": [],
            "createdDate": created_date,
            "deleted": False,
        },
    }

    finish_res = session.post(
        f"{LIMEWIRE_API_BASE_URL}/sharing/upload",
        headers={"Content-Type": "application/json", **claim_headers},
        data=json.dumps(finish_payload),
        timeout=30,
    )
    _raise_for_limewire_error(finish_res, "upload finalization")

    return (
        f"{LIMEWIRE_LINK_BASE_URL}?claimToken={quote(claim_token, safe='')}"
        f"&sharingBucketId={quote(bucket_id, safe='')}"
    )


async def main(prog=None, argv=None):
    args = get_args(prog=prog)

    if args == 0:
        return

    clipboard = None
    if args.clip:
        try:
            import clipboard as clipboard_module
        except Exception as exc:
            print(f"Clipboard support is unavailable: {exc}", file=sys.stderr)
            return 1
        clipboard = clipboard_module

    if not args.name and args.file:
        args.name = os.path.basename(args.file)
    elif not args.name and args.tar:
        args.name = os.path.basename(args.tar) + (".tgz" if args.gzip else ".tar")
    elif not args.name:
        args.name = "stdin-upload.bin"

    try:
        expires_at = _parse_expires_to_iso(args.expires)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        upload_path, upload_size, cleanup_upload_path = _materialize_upload_input(args)
    except Exception as exc:
        print(f"Failed to prepare upload input: {exc}", file=sys.stderr)
        return 1

    try:
        if args.upload_times > 1:

            def upload_file() -> str:
                assert not isinstance(args, int)
                return _limewire_upload_once(
                    file_path=upload_path,
                    file_size=upload_size,
                    file_name=args.name or "file",
                    expires_at=expires_at,
                    quiet=True,
                )

            tasks = [asyncio.to_thread(upload_file) for _ in range(args.upload_times)]
            try:
                links = await asyncio.gather(*tasks)
            except Exception as exc:
                print(exc, file=sys.stderr)
                return 1

            links_s = "\n".join(links)
            print(links_s)
            if args.clip:
                assert clipboard is not None
                clipboard.copy(links_s)
            return

        try:
            link = _limewire_upload_once(
                file_path=upload_path,
                file_size=upload_size,
                file_name=args.name or "file",
                expires_at=expires_at,
                quiet=bool(args.quiet),
            )
        except KeyboardInterrupt:
            print("aborted.", file=sys.stderr)
            return 1
        except Exception as exc:
            print(exc, file=sys.stderr)
            return 1

        if args.clip:
            assert clipboard is not None
            print(link, "(copied to clipboard)")
            clipboard.copy(link)
        else:
            print(link)
    finally:
        cleanup_upload_path()


# _entry_point = lambda: sys.exit(main())
def main_sync():
    result = asyncio.run(main())
    return 0 if result is None else result


if __name__ == "__main__":
    # _entry_point()
    sys.exit(main_sync())
