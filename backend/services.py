"""Object storage (S3-compatible, with local-disk fallback) + Resend email + templates.

This module used to depend on Emergent's proprietary object-storage service. It has been
rewritten to use a standard S3-compatible API (AWS S3, Cloudflare R2, DigitalOcean Spaces,
Backblaze B2, MinIO, etc. all work) via boto3, so the backend can run anywhere — including
Render. If no S3 credentials are configured, it falls back to local disk storage, which is
fine for local development but is EPHEMERAL on most PaaS hosts (files are lost on redeploy
or restart) — configure S3_* env vars for anything you deploy to production.
"""
import os
import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger("posterzone.services")

# ---------- Object storage ----------
LOCAL_STORAGE_DIR = Path(os.environ.get("LOCAL_STORAGE_DIR", str(Path(__file__).parent / "uploads")))

_s3_client = None
_storage_mode: Optional[str] = None  # "s3" | "local"


def _s3_configured() -> bool:
    return bool(os.environ.get("S3_BUCKET"))


def init_storage() -> str:
    """Idempotently set up storage. Returns '"s3"' or '"local"'."""
    global _s3_client, _storage_mode
    if _storage_mode:
        return _storage_mode

    if _s3_configured():
        import boto3  # local import keeps boto3 optional if never used

        _s3_client = boto3.client(
            "s3",
            region_name=os.environ.get("S3_REGION", "auto"),
            endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
            aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY"),
        )
        _storage_mode = "s3"
        log.info("Object storage initialized (S3-compatible, bucket=%s)", os.environ["S3_BUCKET"])
    else:
        LOCAL_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        _storage_mode = "local"
        log.warning(
            "S3_BUCKET not set — using local disk storage at %s. "
            "This is EPHEMERAL on most hosts (Render included) — set S3_BUCKET/"
            "S3_ACCESS_KEY_ID/S3_SECRET_ACCESS_KEY for persistent uploads in production.",
            LOCAL_STORAGE_DIR,
        )
    return _storage_mode


def put_object(path: str, data: bytes, content_type: str) -> dict:
    mode = init_storage()
    if mode == "s3":
        bucket = os.environ["S3_BUCKET"]
        _s3_client.put_object(Bucket=bucket, Key=path, Body=data, ContentType=content_type)
        return {"path": path, "size": len(data)}
    fp = LOCAL_STORAGE_DIR / path
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_bytes(data)
    return {"path": path, "size": len(data)}


def get_object(path: str) -> Tuple[bytes, str]:
    mode = init_storage()
    if mode == "s3":
        bucket = os.environ["S3_BUCKET"]
        obj = _s3_client.get_object(Bucket=bucket, Key=path)
        return obj["Body"].read(), obj.get("ContentType", "application/octet-stream")
    fp = LOCAL_STORAGE_DIR / path
    if not fp.exists():
        raise FileNotFoundError(path)
    ext = fp.suffix.lstrip(".").lower()
    return fp.read_bytes(), MIME_TYPES.get(ext, "application/octet-stream")


MIME_TYPES = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp",
}


def guess_ext(filename: str, content_type: str) -> str:
    if "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext in MIME_TYPES:
            return ext
    for ext, mt in MIME_TYPES.items():
        if mt == content_type:
            return ext
    return "bin"


# ---------- Resend email ----------
def _rk() -> str:
    return os.environ.get("RESEND_API_KEY", "")


async def send_email(to_email: str, subject: str, html: str) -> Optional[str]:
    """Send email via Resend. Returns email id, or None if key missing/failure."""
    key = _rk()
    if not key:
        log.info(f"[email skipped — no RESEND_API_KEY] to={to_email} subject={subject!r}")
        return None
    import resend  # local import keeps resend optional if never used

    resend.api_key = key
    sender = os.environ.get("SENDER_EMAIL", "onboarding@resend.dev")
    params = {"from": sender, "to": [to_email], "subject": subject, "html": html}
    try:
        result = await asyncio.to_thread(resend.Emails.send, params)
        log.info(f"Email sent to {to_email} id={result.get('id')}")
        return result.get("id")
    except Exception as e:
        log.error(f"Email send failed to {to_email}: {e}")
        return None


# ---------- Templates ----------
BRAND = "Poster Zone"
ACCENT = "#FF3B30"
INK = "#0A0A0A"


def _fmt_inr(n) -> str:
    return f"₹{int(n):,}"


def _base(inner_html: str, title: str) -> str:
    return f"""
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body style="margin:0;padding:0;background:#FAFAFA;font-family:Helvetica,Arial,sans-serif;color:{INK};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#FAFAFA;padding:40px 0;">
  <tr><td align="center">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid #E4E4E7;">
      <tr><td style="padding:32px 40px;border-bottom:1px solid #E4E4E7;">
        <div style="font-family:Georgia,serif;font-size:32px;letter-spacing:-0.02em;">Poster<span style="color:{ACCENT};">.</span>Zone</div>
      </td></tr>
      <tr><td style="padding:40px;">{inner_html}</td></tr>
      <tr><td style="padding:24px 40px;background:{INK};color:#ffffff;font-size:12px;">
        <div style="opacity:0.7;">© {BRAND} — Printed. Framed. Shipped.</div>
        <div style="opacity:0.5;margin-top:4px;">hello@posterzone.in</div>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>
"""


def order_placed_html(order: dict) -> str:
    addr = order.get("shipping_address", {})
    rows = "".join(
        f"""<tr>
          <td style="padding:12px 0;border-bottom:1px solid #F4F4F5;width:56px;">
            <img src="{i['image_url']}" alt="" width="56" style="display:block;border:1px solid #E4E4E7;" />
          </td>
          <td style="padding:12px 12px;border-bottom:1px solid #F4F4F5;font-size:14px;">
            <div style="font-weight:600;">{i['title']}</div>
            <div style="color:#52525B;font-size:12px;margin-top:2px;">{i['size']} · {i['frame']} × {i['quantity']}</div>
          </td>
          <td style="padding:12px 0;border-bottom:1px solid #F4F4F5;font-size:14px;text-align:right;">{_fmt_inr(i['unit_price']*i['quantity'])}</td>
        </tr>"""
        for i in order.get("items", [])
    )
    inner = f"""
      <div style="font-family:Georgia,serif;font-size:36px;line-height:1.05;letter-spacing:-0.02em;">On the way<span style="color:{ACCENT};">.</span></div>
      <p style="color:#52525B;line-height:1.6;margin:16px 0;">
        Thanks {addr.get('name','')}, we've received your order and are packing it now.
        Your art will be dispatched within 2 business days.
      </p>
      <div style="margin-top:24px;text-transform:uppercase;letter-spacing:0.18em;font-size:11px;color:#52525B;">Order</div>
      <div style="font-size:18px;font-weight:600;margin-top:4px;">{order.get('order_id')}</div>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:24px;">{rows}</table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:16px;font-size:14px;">
        <tr><td style="color:#52525B;padding:4px 0;">Subtotal</td><td style="text-align:right;padding:4px 0;">{_fmt_inr(order.get('subtotal',0))}</td></tr>
        <tr><td style="color:#52525B;padding:4px 0;">Shipping</td><td style="text-align:right;padding:4px 0;">{'Free' if order.get('shipping',0)==0 else _fmt_inr(order.get('shipping',0))}</td></tr>
        <tr><td style="padding:10px 0 4px;border-top:1px solid #E4E4E7;font-weight:600;">Total</td><td style="text-align:right;padding:10px 0 4px;border-top:1px solid #E4E4E7;font-weight:600;">{_fmt_inr(order.get('total',0))}</td></tr>
      </table>

      <div style="margin-top:32px;text-transform:uppercase;letter-spacing:0.18em;font-size:11px;color:#52525B;">Shipping to</div>
      <div style="font-size:14px;margin-top:6px;line-height:1.5;">
        {addr.get('name','')}<br/>
        {addr.get('address','')}<br/>
        {addr.get('city','')}, {addr.get('state','')} {addr.get('pincode','')}<br/>
        {addr.get('phone','')}
      </div>
    """
    return _base(inner, f"Order {order.get('order_id')} confirmed")


def order_shipped_html(order: dict) -> str:
    addr = order.get("shipping_address", {})
    inner = f"""
      <div style="font-family:Georgia,serif;font-size:36px;line-height:1.05;letter-spacing:-0.02em;">Shipped<span style="color:{ACCENT};">.</span></div>
      <p style="color:#52525B;line-height:1.6;margin:16px 0;">
        Great news {addr.get('name','')} — your Poster Zone order is on its way. It should reach you within 3-5 business days.
      </p>
      <div style="margin-top:24px;text-transform:uppercase;letter-spacing:0.18em;font-size:11px;color:#52525B;">Order</div>
      <div style="font-size:18px;font-weight:600;margin-top:4px;">{order.get('order_id')}</div>
      <p style="color:#52525B;line-height:1.6;margin-top:24px;">
        Once your prints arrive, take a photo and tag <b>@posterzone</b> on Instagram — we love seeing them on your walls.
      </p>
    """
    return _base(inner, f"Order {order.get('order_id')} shipped")
