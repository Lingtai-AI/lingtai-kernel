"""Media download/upload helpers for WeChat addon."""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import api
from .types import (
    CDNMedia, UploadMediaType, MessageItemType,
    ImageItem, VoiceItem, FileItem, VideoItem, MessageItem,
)


@dataclass
class UploadedMediaInfo:
    """The full result of a CDN upload, carrying everything sendMessage needs.

    `upload_media` returns this so non-image media (video, file) can populate
    their type-specific size fields — OpenClaw sets `video_item.video_size` to
    the ciphertext byte count and `file_item.len` to the plaintext byte count,
    in addition to `image_item.mid_size`. Without those, the WeChat client may
    accept the message but fail to render or download the attachment.
    """

    cdn_media: CDNMedia
    media_type: UploadMediaType
    raw_size: int          # plaintext byte count
    ciphertext_size: int   # AES-128-ECB + PKCS#7 byte count
    filekey: str

log = logging.getLogger(__name__)

# The iLink get-upload request uses api.DEFAULT_SEND_TIMEOUT (15 seconds).
# Tencent v1.0.3 permits three immediate CDN attempts; each LingTai request is
# independently bounded at 120 seconds. These constants also feed the manager's
# complete-coroutine deadline rather than leaving mismatched magic numbers.
GET_UPLOAD_URL_TIMEOUT_SECONDS = api.DEFAULT_SEND_TIMEOUT
CDN_UPLOAD_TIMEOUT_SECONDS = 120.0
CDN_UPLOAD_MAX_ATTEMPTS = 3


@dataclass
class OutboundMediaError(Exception):
    """Redacted outbound upload failure with a machine-readable stage.

    ``upload_media`` constructs or receives a secret-bearing CDN URL. This
    exception intentionally retains only its hostname; path, query, fragment,
    token, recipient ID, and local file path must never cross the tool boundary.
    """

    stage: str
    message: str
    endpoint_host: str | None = None
    retryable: bool = False
    remote_acceptance: str | None = None

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "stage": self.stage,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.endpoint_host:
            result["endpoint_host"] = self.endpoint_host
        if self.remote_acceptance:
            result["remote_acceptance"] = self.remote_acceptance
        return result


def _safe_hostname(url: str) -> str | None:
    """Return only a normalized hostname from a CDN URL."""
    try:
        return urlsplit(url).hostname
    except (TypeError, ValueError):
        return None


def _valid_cdn_base_url(url: str) -> bool:
    """Accept a configured HTTP(S) origin/path with no secret query material."""
    if not isinstance(url, str) or not url or url != url.strip():
        return False
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def _valid_dynamic_upload_url(url: str) -> bool:
    """Require an absolute HTTP(S) fallback while allowing its signed query."""
    if not isinstance(url, str) or not url or url != url.strip():
        return False
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    )


def _official_cdn_upload_url(
    cdn_base_url: str, upload_param: str, filekey: str,
) -> str:
    """Build Tencent's v1.0.3 upload route with exact component encoding."""
    encode_uri_component_safe = "-_.!~*'()"
    return (
        f"{cdn_base_url.rstrip('/')}/upload?encrypted_query_param="
        f"{quote(upload_param, safe=encode_uri_component_safe)}&filekey="
        f"{quote(filekey, safe=encode_uri_component_safe)}"
    )


def _usable_encrypted_reference(value: object) -> str | None:
    """Return only a nonblank string media reference from CDN metadata."""
    return value if isinstance(value, str) and value.strip() else None


def _cdn_transport_failure(exc: httpx.HTTPError, upload_url: str) -> OutboundMediaError:
    """Map an httpx upload failure without retaining its secret request URL."""
    if isinstance(exc, httpx.TimeoutException):
        message = "Timed out while connecting to or uploading bytes to the WeChat CDN."
    else:
        message = "Could not connect to or upload bytes to the WeChat CDN."
    return OutboundMediaError(
        stage="cdn_upload_transport",
        message=message,
        endpoint_host=_safe_hostname(upload_url),
        retryable=True,
    )


def media_message_failure(exc: Exception) -> OutboundMediaError:
    """Redact a failure while sending the final iLink media message.

    Transport failures and retryable HTTP statuses do not prove non-delivery:
    iLink may have accepted the message before the response was lost. Keep that
    ambiguity explicit and never recommend an automatic whole-message retry.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        ambiguous = status_code >= 500 or status_code == 429
        return OutboundMediaError(
            stage="media_message_http",
            message=f"WeChat iLink rejected the media message (HTTP {status_code}).",
            retryable=False,
            remote_acceptance="unknown" if ambiguous else "rejected",
        )
    if isinstance(exc, (httpx.HTTPError, TimeoutError)):
        return OutboundMediaError(
            stage="media_message_transport",
            message="Could not reach WeChat iLink while sending the media message.",
            retryable=False,
            remote_acceptance="unknown",
        )
    return OutboundMediaError(
        stage="media_message_response",
        message="WeChat iLink did not accept the media message.",
        retryable=False,
        remote_acceptance="rejected",
    )


# ── Magic-byte validation ──────────────────────────────────────────────────
#
# WeChat attachments sometimes arrive under a normal-looking name/extension
# (".pdf", ".zip", ".jpg") while the bytes saved under wechat/media/ are
# encrypted / cache / private-container data rather than the real exported
# file. Downstream agents then fail late in PDF/ZIP/vision tooling with
# confusing errors. We validate the saved bytes against the declared
# extension's magic bytes right after download and surface a structured
# warning so the agent can ask the user to re-export ("Save As") instead of
# repeatedly running parsers on unusable bytes. The raw bytes are always
# preserved on disk for inspection — we only annotate, never delete.

# Declared extension → list of acceptable leading-byte signatures.
# Signatures are matched at the start of the file (RIFF/WEBP needs a windowed
# check, handled below).
_MAGIC_SIGNATURES: dict[str, list[bytes]] = {
    ".pdf": [b"%PDF-"],
    ".zip": [
        b"PK\x03\x04",  # normal local file header
        b"PK\x05\x06",  # empty archive (end-of-central-directory only)
        b"PK\x07\x08",  # spanned archive marker
    ],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".gif": [b"GIF87a", b"GIF89a"],
    ".bmp": [b"BM"],
    # .webp is a RIFF container: "RIFF" .... "WEBP"; checked specially.
    ".webp": [b"RIFF"],
}

# How many bytes we need to read to cover the longest signature + WEBP window.
_MAGIC_READ_LEN = 32

_RECOVERY_HINT = (
    "This attachment's bytes do not match its '{ext}' extension — it is "
    "likely WeChat/QQ cache, encrypted, or private-container data rather "
    "than the real exported file. Do not run PDF/ZIP/image parsers on it. "
    "Ask the sender to open WeChat and use right-click / long-press → "
    "\"Save As\" to export the original file, or send it via a cloud-drive "
    "link / original file upload."
)


@dataclass
class MediaValidation:
    """Result of validating saved attachment bytes against the declared
    extension's magic bytes.

    status:
      - "ok":       bytes match the declared type's signature.
      - "mismatch": a signature is known for the extension but the bytes
                    don't match it — likely cache/encrypted/private data.
      - "unknown":  no signature is known for this extension (or no
                    extension), so no judgement is made. Never a warning.
    """

    status: str
    declared_ext: str
    path: str
    warning: str | None = None
    hint: str | None = None

    def render_suffix(self) -> str:
        """Compact inline annotation for the conversation body. Empty unless
        this is a mismatch the agent should be warned about."""
        if self.status != "mismatch":
            return ""
        return f" ⚠ WARNING: {self.warning} {self.hint}"


_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")


def _matches_signature(head: bytes, ext: str) -> bool:
    sigs = _MAGIC_SIGNATURES.get(ext, [])
    if ext == ".webp":
        # RIFF container with a "WEBP" form type at offset 8.
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return any(head.startswith(sig) for sig in sigs)


def _matches_any_image_signature(head: bytes) -> bool:
    return any(_matches_signature(head, ext) for ext in _IMAGE_EXTS)


def _read_magic_head(path: Path) -> bytes | None:
    try:
        with path.open("rb") as fh:
            return fh.read(_MAGIC_READ_LEN)
    except OSError as e:
        log.warning("validate_media_bytes: cannot read %s: %s", path, e)
        return None


def validate_image_bytes(file_path: str | Path) -> MediaValidation:
    """Validate an inbound WeChat IMAGE download as any known image type.

    WeChat IMAGE payloads are saved under generated ``.jpg`` names because the
    protocol item does not reliably carry the original extension. Valid PNG,
    WebP, GIF, or BMP bytes must therefore not be rejected merely because the
    fabricated filename ends with ``.jpg``. This generic image check still flags
    cache/encrypted/private-container bytes that are not any recognized image.
    """
    path = Path(file_path)
    head = _read_magic_head(path)
    if head is None:
        return MediaValidation(status="unknown", declared_ext="image", path=str(path))
    if _matches_any_image_signature(head):
        return MediaValidation(status="ok", declared_ext="image", path=str(path))

    warning = (
        f"saved '{path.name}' is not a recognized image file "
        "(magic bytes do not match known image signatures)"
    )
    return MediaValidation(
        status="mismatch",
        declared_ext="image",
        path=str(path),
        warning=warning,
        hint=_RECOVERY_HINT.format(ext="image"),
    )


def validate_media_bytes(file_path: str | Path) -> MediaValidation:
    """Validate a saved media file's bytes against its declared extension.

    Returns a MediaValidation. Never raises for content reasons — a missing
    or unreadable file is reported as "unknown" so validation can never break
    the download path itself.
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext not in _MAGIC_SIGNATURES:
        return MediaValidation(status="unknown", declared_ext=ext, path=str(path))

    head = _read_magic_head(path)
    if head is None:
        return MediaValidation(status="unknown", declared_ext=ext, path=str(path))

    if _matches_signature(head, ext):
        return MediaValidation(status="ok", declared_ext=ext, path=str(path))

    kind = ext.lstrip(".")
    warning = (
        f"saved '{path.name}' is not a valid {kind} file "
        f"(magic bytes do not match {ext})"
    )
    return MediaValidation(
        status="mismatch",
        declared_ext=ext,
        path=str(path),
        warning=warning,
        hint=_RECOVERY_HINT.format(ext=ext),
    )


# Extension → UploadMediaType mapping
_UPLOAD_TYPE_MAP = {
    ".jpg": UploadMediaType.IMAGE,
    ".jpeg": UploadMediaType.IMAGE,
    ".png": UploadMediaType.IMAGE,
    ".gif": UploadMediaType.IMAGE,
    ".webp": UploadMediaType.IMAGE,
    ".bmp": UploadMediaType.IMAGE,
    ".mp4": UploadMediaType.VIDEO,
    ".avi": UploadMediaType.VIDEO,
    ".mov": UploadMediaType.VIDEO,
    ".mkv": UploadMediaType.VIDEO,
    ".wav": UploadMediaType.VOICE,
    ".mp3": UploadMediaType.VOICE,
    ".ogg": UploadMediaType.VOICE,
    ".silk": UploadMediaType.VOICE,
    ".amr": UploadMediaType.VOICE,
}

# UploadMediaType → MessageItemType mapping
_ITEM_TYPE_MAP = {
    UploadMediaType.IMAGE: MessageItemType.IMAGE,
    UploadMediaType.VIDEO: MessageItemType.VIDEO,
    UploadMediaType.VOICE: MessageItemType.VOICE,
    UploadMediaType.FILE: MessageItemType.FILE,
}


async def download_media(
    cdn_media: CDNMedia,
    dest_dir: str | Path,
    filename: str = "media",
) -> str:
    """Download media from CDN. Returns local file path."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    url = cdn_media.full_url
    if not url:
        raise ValueError("CDN media has no full_url")

    # Sanitize: use basename only to prevent path traversal via sender-controlled filenames
    safe_name = Path(filename).name or "file"
    dest_path = dest_dir / safe_name
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=60.0)
        resp.raise_for_status()
        dest_path.write_bytes(resp.content)

    return str(dest_path)


def decode_voice(silk_path: str | Path, out_path: str | Path) -> str:
    """Decode Silk audio to WAV. Returns output path.

    Requires the `pilk` package: pip install pilk
    """
    try:
        import pilk
    except ImportError:
        log.warning("pilk not installed — cannot decode Silk voice. pip install pilk")
        return str(silk_path)

    silk_path = str(silk_path)
    out_path = str(out_path)
    pilk.decode(silk_path, out_path)
    return out_path


def detect_upload_type(file_path: str | Path) -> UploadMediaType:
    """Detect UploadMediaType from file extension. Defaults to FILE."""
    ext = Path(file_path).suffix.lower()
    return _UPLOAD_TYPE_MAP.get(ext, UploadMediaType.FILE)


def _pkcs7_pad(data: bytes, block: int = 16) -> bytes:
    pad = block - (len(data) % block)
    return data + bytes([pad]) * pad


def _aes128_ecb_encrypt(data: bytes, key: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    enc = cipher.encryptor()
    return enc.update(_pkcs7_pad(data, 16)) + enc.finalize()


def _encrypted_size(raw_size: int, block: int = 16) -> int:
    # PKCS#7 always appends at least one block when already aligned.
    return raw_size + (block - (raw_size % block))


async def upload_media(
    file_path: str | Path,
    base_url: str,
    token: str,
    to_user_id: str,
    *,
    cdn_base_url: str = api.CDN_BASE_URL,
) -> UploadedMediaInfo:
    """Upload a file to WeChat CDN.

    Returns an UploadedMediaInfo carrying the CDNMedia reference plus the
    raw/ciphertext sizes and filekey that non-image media (video, file)
    need to populate their type-specific size fields downstream.

    Mirrors Hermes/OpenClaw: iLink expects getuploadurl to receive raw size,
    raw MD5, AES key, and padded ciphertext size; the CDN upload body is
    AES-128-ECB encrypted with PKCS#7 padding and posted with HTTP POST
    (not PUT); sendMessage then references encrypt_query_param + aes_key +
    encrypt_type=1. Earlier versions of this addon used plaintext PUT, which
    iLink would accept (HTTP 200) but produce an image the WeChat client
    could not decrypt/open.

    The final download parameter MUST come from the CDN's `x-encrypted-param`
    response header (or, as a documented fallback, a JSON body containing
    `encrypt_query_param` / `download_param`). Falling back to the
    pre-upload `upload_param` or to the locally-generated `filekey` would
    silently recreate the prior "sendMessage returns ok but WeChat client
    can't open the image" false-positive — those values are NOT the
    download parameter and the WeChat client cannot decrypt the payload
    with them. So we raise instead.
    """
    file_path = Path(file_path)
    if not file_path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    data = file_path.read_bytes()
    md5 = hashlib.md5(data).hexdigest()
    media_type = detect_upload_type(file_path)
    aeskey = secrets.token_bytes(16)
    aeskey_hex = aeskey.hex()
    ciphertext = _aes128_ecb_encrypt(data, aeskey)
    filekey = secrets.token_hex(16)

    # Get upload parameters. filesize is ciphertext size, rawsize is plaintext.
    # Official Tencent v1.0.3 requires upload_param and constructs the CDN URL
    # from the configured base. LingTai's extra upload_full_url is retained only
    # as compatibility fallback when that official parameter is absent.
    try:
        upload_resp = await api.get_upload_url(
            base_url, token,
            media_type=int(media_type),
            to_user_id=to_user_id,
            rawsize=len(data),
            rawfilemd5=md5,
            filesize=len(ciphertext),
            aeskey=aeskey_hex,
            filekey=filekey,
            no_need_thumb=True,
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        raise OutboundMediaError(
            stage="get_upload_url_http",
            message=f"WeChat iLink rejected the upload-URL request (HTTP {status_code}).",
            retryable=status_code >= 500 or status_code == 429,
        ) from None
    except httpx.HTTPError as exc:
        raise OutboundMediaError(
            stage="get_upload_url_transport",
            message="Could not reach WeChat iLink to obtain a media upload URL.",
            retryable=True,
        ) from None
    except Exception:
        raise OutboundMediaError(
            stage="get_upload_url_response",
            message="WeChat iLink returned an unusable upload-URL response.",
            retryable=False,
        ) from None

    raw_upload_param = upload_resp.upload_param
    upload_full_url = (
        upload_resp.upload_full_url
        if isinstance(upload_resp.upload_full_url, str)
        else ""
    )
    if raw_upload_param is not None:
        if not isinstance(raw_upload_param, str) or not raw_upload_param:
            raise OutboundMediaError(
                stage="get_upload_url_response",
                message="WeChat iLink returned unusable media upload parameters.",
                retryable=False,
            )
        if not _valid_cdn_base_url(cdn_base_url):
            raise OutboundMediaError(
                stage="get_upload_url_response",
                message="WeChat iLink returned unusable media upload parameters.",
                retryable=False,
            )
        upload_url = _official_cdn_upload_url(
            cdn_base_url, raw_upload_param, filekey,
        )
    elif upload_full_url and _valid_dynamic_upload_url(upload_full_url):
        upload_url = upload_full_url
    else:
        raise OutboundMediaError(
            stage="get_upload_url_response",
            message="WeChat iLink did not return usable media upload parameters.",
            retryable=False,
        )

    # Retry only this encrypted CDN POST, never the surrounding text+media send.
    # Match Tencent v1.0.3's no-backoff behavior and retry HTTP 200 responses
    # missing encrypted metadata: such a response is not a usable completion.
    # Provider x-error-message and response bodies are never copied into errors.
    download_param: str | None = None
    last_failure: OutboundMediaError | None = None
    try:
        client_context = httpx.AsyncClient()
    except Exception:
        raise OutboundMediaError(
            stage="cdn_upload_response",
            message="The WeChat CDN upload request could not be prepared.",
            endpoint_host=_safe_hostname(upload_url),
            retryable=False,
        ) from None
    try:
        async with client_context as client:
            for attempt in range(1, CDN_UPLOAD_MAX_ATTEMPTS + 1):
                try:
                    resp = await client.post(
                        upload_url,
                        content=ciphertext,
                        headers={"Content-Type": "application/octet-stream"},
                        timeout=CDN_UPLOAD_TIMEOUT_SECONDS,
                    )
                except httpx.HTTPError as exc:
                    last_failure = _cdn_transport_failure(exc, upload_url)
                except Exception:
                    # httpx.InvalidURL is not an HTTPError. Keep any unexpected
                    # client/request construction detail behind the response stage.
                    raise OutboundMediaError(
                        stage="cdn_upload_response",
                        message="The WeChat CDN upload request could not be prepared.",
                        endpoint_host=_safe_hostname(upload_url),
                        retryable=False,
                    ) from None
                else:
                    status_code = resp.status_code
                    if status_code != 200:
                        retryable = status_code == 429 or status_code >= 500
                        last_failure = OutboundMediaError(
                            stage="cdn_upload_http",
                            message=(
                                "The WeChat CDN rejected the media upload "
                                f"(HTTP {status_code})."
                            ),
                            endpoint_host=_safe_hostname(upload_url),
                            retryable=retryable,
                        )
                        if not retryable:
                            raise last_failure from None
                    else:
                        header_param = _usable_encrypted_reference(
                            resp.headers.get("x-encrypted-param")
                        )
                        json_param: str | None = None
                        raw = resp.text
                        if raw and raw.strip().startswith("{"):
                            try:
                                body = resp.json()
                                json_param = _usable_encrypted_reference(
                                    body.get("encrypt_query_param")
                                ) or _usable_encrypted_reference(
                                    body.get("download_param")
                                )
                            except Exception:
                                json_param = None
                        download_param = header_param or json_param
                        if download_param:
                            break
                        last_failure = OutboundMediaError(
                            stage="cdn_upload_response",
                            message=(
                                "The WeChat CDN accepted the upload but did not "
                                "return the required encrypted media reference."
                            ),
                            endpoint_host=_safe_hostname(upload_url),
                            retryable=True,
                        )

                if attempt == CDN_UPLOAD_MAX_ATTEMPTS:
                    assert last_failure is not None
                    raise last_failure from None
    except OutboundMediaError:
        raise
    except Exception:
        raise OutboundMediaError(
            stage="cdn_upload_response",
            message="The WeChat CDN upload request could not be prepared.",
            endpoint_host=_safe_hostname(upload_url),
            retryable=False,
        ) from None

    if not download_param:  # defensive: loop exits only on a usable reference
        raise OutboundMediaError(
            stage="cdn_upload_response",
            message="The WeChat CDN did not return a usable media reference.",
            endpoint_host=_safe_hostname(upload_url),
            retryable=False,
        )

    cdn_media = CDNMedia(
        encrypt_query_param=download_param,
        # OpenClaw sends media.aes_key as base64(32-char hex string), not
        # base64(raw 16 bytes). The WeChat client decrypts the CDN payload
        # using this exact form; the raw-bytes form looks valid but renders
        # as an un-openable image.
        aes_key=base64.b64encode(aeskey_hex.encode("ascii")).decode("ascii"),
        encrypt_type=1,
    )
    return UploadedMediaInfo(
        cdn_media=cdn_media,
        media_type=media_type,
        raw_size=len(data),
        ciphertext_size=len(ciphertext),
        filekey=filekey,
    )


def make_media_item(info: UploadedMediaInfo, file_path: Path) -> MessageItem:
    """Create a MessageItem for sending uploaded media.

    For each media type, sets the OpenClaw/Hermes size fields that the
    WeChat client requires to render/download the attachment:

    - image: ``image_item.mid_size = ciphertext byte count``
    - video: ``video_item.video_size = ciphertext byte count``
    - file:  ``file_item.len = str(plaintext byte count)``
    - voice: no extra size field documented in OpenClaw outbound; the
      iLink schema does include ``playtime`` and ``encode_type`` if you
      have them, but outbound voice send is not part of the validated
      path. The MessageItem still carries the encrypted CDN reference,
      so a downstream client with looser validation may render it.
    """
    item_type = _ITEM_TYPE_MAP.get(info.media_type, MessageItemType.FILE)
    item = MessageItem(type=int(item_type))
    if item_type == MessageItemType.IMAGE:
        item.image_item = ImageItem(
            media=info.cdn_media,
            mid_size=info.ciphertext_size,
        )
    elif item_type == MessageItemType.VIDEO:
        item.video_item = VideoItem(
            media=info.cdn_media,
            video_size=info.ciphertext_size,
        )
    elif item_type == MessageItemType.VOICE:
        item.voice_item = VoiceItem(media=info.cdn_media)
    else:
        item.file_item = FileItem(
            media=info.cdn_media,
            file_name=file_path.name,
            len=str(info.raw_size),
        )

    return item
