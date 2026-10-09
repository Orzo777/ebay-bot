"""Публікація оголошення на eBay.de з бота «Облік і продаж» (09.10).

Потік: «ebay вхід» у боті → посилання на сторінку дозволу eBay → користувач входить і тисне «Agree» →
адресу сторінки, куди eBay перекинув (з ?code=…), вставляє в бот → тут код міняємо на ключ (refresh token, 18 міс.),
ключ шифровано повертаємо в Apps Script (Script Properties) — у GitHub і в журналах його немає.
Далі кнопка «🚀 Виставити» на картці «продати N»: фото з обліку → eBay (UploadSiteHostedPictures) →
VerifyAddFixedPriceItem (помилки — у бот, нічого не публікуємо) → AddFixedPriceItem → посилання + «Виставлено» в обліку.
Trading API (XML): доставка й повернення задаються прямо в оголошенні, без окремих «Rahmenbedingungen».
Мережа — лише через переданий post (у тестах — заглушка).
"""
import base64
import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, unquote, urlencode, urlparse

AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
TRADING_URL = "https://api.ebay.com/ws/api.dll"
SCOPES = ["https://api.ebay.com/oauth/api_scope", "https://api.ebay.com/oauth/api_scope/sell.inventory",
          "https://api.ebay.com/oauth/api_scope/sell.account", "https://api.ebay.com/oauth/api_scope/sell.fulfillment"]
SITE_DE, COMPAT = "77", "1311"
NS = "{urn:ebay:apis:eBLBaseComponents}"
CATEGORY = {"ram": "170083", "console": "139971"}   # Taxonomy API 09.10: «Arbeitsspeicher (RAM)», «Konsolen»
CONDITION_USED = "3000"                              # «Gebraucht» — дозволено в обох категоріях (Metadata API 09.10)
SHIP_SERVICE = "DE_DHLPaket"
MAX_PHOTOS = 12
LOCATION = "Hamburg"


class EbayError(Exception):
    pass


class AuthError(EbayError):
    """Ключ недійсний / прострочений — треба «ebay вхід» ще раз."""


def _basic(app_id: str, cert_id: str) -> str:
    return "Basic " + base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()


def consent_url(app_id: str, runame: str, state: str = "ebaybot") -> str:
    return AUTH_URL + "?" + urlencode({"client_id": app_id, "redirect_uri": runame, "response_type": "code",
                                       "scope": " ".join(SCOPES), "state": state})


def code_from(text: str) -> str | None:
    """Код дозволу з адреси, яку користувач вставив у бот (або сам код «v^1.1#…»)."""
    text = (text or "").strip()
    m = re.search(r"https?://\S+", text)
    if m:
        q = parse_qs(urlparse(m.group(0)).query)
        if q.get("code"):
            return q["code"][0]
    m = re.search(r"(?:^|[?&\s])code=([^&\s]+)", text)
    if m:
        return unquote(m.group(1))
    m = re.search(r"\bv\^1\.1#\S+", text)
    return m.group(0) if m else None


def _token_call(data: dict, app_id: str, cert_id: str, post) -> dict:
    r = post(TOKEN_URL, headers={"Content-Type": "application/x-www-form-urlencoded", "Authorization": _basic(app_id, cert_id)},
             data=data, timeout=20)
    try:
        j = r.json()
    except ValueError:
        j = {}
    if r.status_code != 200:
        msg = j.get("error_description") or j.get("error") or f"HTTP {r.status_code}"
        raise (AuthError if j.get("error") in ("invalid_grant", "invalid_request") else EbayError)(msg)
    return j


def exchange_code(code: str, app_id: str, cert_id: str, runame: str, post) -> tuple[str, int]:
    """Код (дійсний 5 хв) → (refresh token, скільки секунд дійсний)."""
    j = _token_call({"grant_type": "authorization_code", "code": code, "redirect_uri": runame}, app_id, cert_id, post)
    if not j.get("refresh_token"):
        raise EbayError("eBay не дав ключ (refresh_token)")
    return j["refresh_token"], int(j.get("refresh_token_expires_in") or 0)


def access_token(refresh: str, app_id: str, cert_id: str, post) -> str:
    j = _token_call({"grant_type": "refresh_token", "refresh_token": refresh, "scope": " ".join(SCOPES)}, app_id, cert_id, post)
    return j["access_token"]


# ----------------------------------------------------------------------------- Trading API
def _headers(call: str, token: str) -> dict:
    return {"X-EBAY-API-SITEID": SITE_DE, "X-EBAY-API-COMPATIBILITY-LEVEL": COMPAT, "X-EBAY-API-CALL-NAME": call,
            "X-EBAY-API-IAF-TOKEN": token}


def _request(call: str, body: str) -> str:
    return (f'<?xml version="1.0" encoding="utf-8"?><{call}Request xmlns="urn:ebay:apis:eBLBaseComponents">'
            f"<ErrorLanguage>en_US</ErrorLanguage><WarningLevel>High</WarningLevel>{body}</{call}Request>")


def _parse(r) -> ET.Element:
    try:
        return ET.fromstring(r.content)
    except ET.ParseError:
        raise EbayError(f"eBay відповів не XML (HTTP {r.status_code})")


def problems(root: ET.Element) -> tuple[list[str], list[str]]:
    """→ (помилки, попередження) з відповіді Trading API."""
    errs, warns = [], []
    for e in root.iter(NS + "Errors"):
        text = (e.findtext(NS + "LongMessage") or e.findtext(NS + "ShortMessage") or "").strip()
        code = e.findtext(NS + "ErrorCode") or ""
        (errs if e.findtext(NS + "SeverityCode") == "Error" else warns).append(f"{text} [{code}]")
    return errs, warns


def _token_problem(errs: list[str]) -> bool:   # 21916984 / 21917053 / 931 / 932 — недійсний або відкликаний ключ
    return any(re.search(r"\[(?:931|932|21916984|21917053|21916013)\]", e) for e in errs)


def trading(call: str, body: str, token: str, post) -> ET.Element:
    h = dict(_headers(call, token), **{"Content-Type": "text/xml; charset=utf-8"})
    root = _parse(post(TRADING_URL, headers=h, data=_request(call, body).encode("utf-8"), timeout=60))
    errs, _ = problems(root)
    if _token_problem(errs):
        raise AuthError("; ".join(errs))
    return root


def upload_picture(data: bytes, name: str, token: str, post) -> str:
    xml = _request("UploadSiteHostedPictures", f"<PictureName>{html.escape(name)}</PictureName><PictureSet>Supersize</PictureSet>")
    files = [("XML Payload", (None, xml, "text/xml")), ("image", (name + ".jpg", data, "application/octet-stream"))]
    root = _parse(post(TRADING_URL, headers=_headers("UploadSiteHostedPictures", token), files=files, timeout=60))
    url = root.findtext(f"{NS}SiteHostedPictureDetails/{NS}FullURL")
    if not url:
        errs, _ = problems(root)
        if _token_problem(errs):
            raise AuthError("; ".join(errs))
        raise EbayError("фото не прийнято: " + ("; ".join(errs) or "без пояснення"))
    return url


def is_image(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _x(v) -> str:
    return html.escape(str(v), quote=False)


def description_html(desc: str) -> str:
    return ('<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.5">'
            + "<br>".join(_x(line) for line in desc.split("\n")) + "</div>")


def specifics(tx: dict) -> list[tuple[str, str]]:
    out = []
    for k, v in list(tx["required"]) + list(tx["more"]):
        if v and v != "—" and k not in dict(out):
            out.append((k, v))
    return out


def item_xml(kind: str, tx: dict, prices: dict, pics: list[str], sku: str, duration: str = "GTC", offers: bool = True) -> str:
    """prices: item (ціна товару без доставки), accept (автоприйняти від), decline (відхиляти нижче) — як у картці."""
    eur = lambda tag, v: f'<{tag} currencyID="EUR">{float(v):.2f}</{tag}>'   # noqa: E731
    spec = "".join(f"<NameValueList><Name>{_x(k)}</Name><Value>{_x(v)}</Value></NameValueList>" for k, v in specifics(tx))
    best = ""
    if offers and prices.get("accept") and prices.get("decline") and prices["decline"] < prices["accept"] < prices["item"]:
        best = ("<BestOfferDetails><BestOfferEnabled>true</BestOfferEnabled></BestOfferDetails>"
                f"<ListingDetails>{eur('BestOfferAutoAcceptPrice', prices['accept'])}{eur('MinimumBestOfferPrice', prices['decline'])}</ListingDetails>")
    return ("<Item>"
            f"<Title>{_x(tx['title'][:80])}</Title>"
            f"<Description>{_x(description_html(tx['desc']))}</Description>"
            f"<PrimaryCategory><CategoryID>{CATEGORY[kind]}</CategoryID></PrimaryCategory>"
            f"{eur('StartPrice', prices['item'])}"
            f"<ConditionID>{CONDITION_USED}</ConditionID><ConditionDescription>{_x(tx['cond'][:1000])}</ConditionDescription>"
            f"<Country>DE</Country><Currency>EUR</Currency><Location>{LOCATION}</Location><Site>Germany</Site>"
            f"<DispatchTimeMax>2</DispatchTimeMax><ListingDuration>{duration}</ListingDuration><ListingType>FixedPriceItem</ListingType>"
            f"<Quantity>1</Quantity><SKU>{_x(sku)}</SKU>"
            "<PictureDetails>" + "".join(f"<PictureURL>{_x(u)}</PictureURL>" for u in pics[:MAX_PHOTOS]) + "</PictureDetails>"
            f"<ItemSpecifics>{spec}</ItemSpecifics>{best}"
            "<ShippingDetails><ShippingType>Flat</ShippingType><ShippingServiceOptions><ShippingServicePriority>1</ShippingServicePriority>"
            f"<ShippingService>{SHIP_SERVICE}</ShippingService>{eur('ShippingServiceCost', tx['ship'])}</ShippingServiceOptions></ShippingDetails>"
            "<ReturnPolicy><ReturnsAcceptedOption>ReturnsNotAccepted</ReturnsAcceptedOption></ReturnPolicy>"
            "</Item>")


def listing_fee(root: ET.Element) -> float:
    for f in root.iter(NS + "Fee"):
        if f.findtext(NS + "Name") == "ListingFee":
            try:
                return float(f.findtext(NS + "Fee") or 0)
            except ValueError:
                return 0.0
    return 0.0


def fee_parts(root: ET.Element) -> dict:
    """Ненульові складові комісій (ListingFee — їхня сума, тому без неї)."""
    out = {}
    for f in root.iter(NS + "Fee"):
        name = f.findtext(NS + "Name") or ""
        try:
            v = float(f.findtext(NS + "Fee") or 0)
        except ValueError:
            continue
        if v and name != "ListingFee":
            out[name] = v
    return out


# відомі попередження eBay → коротко українською (решта — як є)
KNOWN_WARN = [(r"\[21920376\]|Final Value Fee waived", "✅ комісія з продажу (Verkaufsprovision) — 0 €: eBay її знімає"),
              (r"einbehalten|pending", "ℹ️ гроші за перші продажі eBay може притримати, поки покупець не отримає товар "
                                         "(звично для нових продавців)")]


def explain(warns: list[str]) -> list[str]:
    out = []
    for w in warns:
        hit = next((ua for rx, ua in KNOWN_WARN if re.search(rx, w, re.I)), None)
        if (hit or w) not in out:
            out.append(hit or w)
    return out


VARIANTS = [("30 днів замість безстрокового", {"duration": "Days_30"}), ("без Preisvorschlag", {"offers": False}),
            ("30 днів і без Preisvorschlag", {"duration": "Days_30", "offers": False})]


def verify_and_add(kind: str, tx: dict, prices: dict, pics: list[str], sku: str, token: str, post, dry: bool) -> dict:
    """→ {ok, errors, warnings, fee, item_id}. Спершу Verify; Add — лише якщо Verify без помилок і не dry."""
    body = item_xml(kind, tx, prices, pics, sku)
    root = trading("VerifyAddFixedPriceItem", body, token, post)
    errs, warns = problems(root)
    res = {"ok": not errs, "errors": errs, "warnings": warns, "fee": listing_fee(root), "fees": fee_parts(root), "item_id": None}
    if errs:
        return res
    if dry:
        if res["fee"]:   # 09.10: InsertionFee 0,50 € — від чого залежить (Verify безкоштовний, нічого не публікує)
            res["variants"] = {}
            for label, kw in VARIANTS:
                try:
                    r2 = trading("VerifyAddFixedPriceItem", item_xml(kind, tx, prices, pics, sku, **kw), token, post)
                except EbayError:
                    continue
                e2, _ = problems(r2)
                res["variants"][label] = None if e2 else listing_fee(r2)
        return res
    root = trading("AddFixedPriceItem", body, token, post)
    errs, warns2 = problems(root)
    res.update(ok=not errs and bool(root.findtext(NS + "ItemID")), errors=errs, warnings=warns + warns2,
               item_id=root.findtext(NS + "ItemID"), fee=listing_fee(root) or res["fee"], fees=fee_parts(root) or res["fees"])
    return res
