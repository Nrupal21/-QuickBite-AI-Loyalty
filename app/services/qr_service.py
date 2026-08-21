"""QuickBite — Generate branch QR codes (BRANCH-01).

Encodes a branch's bare `qr_code_token` as the QR payload — nothing else.
`static/js/scan.js` reads the browser's `BarcodeDetector.detect()` result
straight off the decoded QR (`codes[0].rawValue`) and posts it unchanged as
`POST /loyalty/scan`'s `qr_token`; wrapping it in a URL or JSON here would
break that exact-match lookup (`Branch.qr_code_token == qr_token` in both
loyalty_service and review_service).

R2 upload is Doc 2's documented storage target for the rendered PNG, but is
not wired here: the image is generated on demand and streamed directly from
the API response, which needs no cloud credentials to work in local dev,
CI, or a fresh clone — swapping in an R2-backed cache is a pure addition
later, not a rework of this contract.
"""

import io

import qrcode
import qrcode.constants


def generate_qr_png(token: str) -> bytes:
    """Render `token` as a PNG QR code, sized for print on a receipt.

    Error correction is `M` (~15% of the code can be damaged/obscured and
    still scan) — receipts fold, smudge, and fade, and the token is short
    enough that `M` costs little density versus the default `L`.
    """
    image = qrcode.make(token, error_correction=qrcode.constants.ERROR_CORRECT_M)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
