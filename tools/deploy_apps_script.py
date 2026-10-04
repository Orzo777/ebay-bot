"""Автооновлення Apps Script з GitHub (04.10): tools/*.gs → проєкт Apps Script → нова версія → вебзастосунок.

Раніше після кожної зміни .gs користувач увечері вручну замінював файл і робив «Нова версія». Тепер це робить
workflow deploy_apps_script.yml після тестів. Доступ — вхід clasp із вузькими правами (лише код і розгортання
Apps Script, без Диска й Google Cloud); вміст ~/.clasprc.json лежить у секреті CLASPRC_JSON, ID проєкту — APPS_SCRIPT_ID.

Безпечно для живого бота:
  • файли проєкту зіставляються з репозиторієм за константою VER_* (code.gs у проєкті = gmail_trigger.gs тут);
    файли без VER_* і HTML лишаються як є — нічого не видаляємо;
  • заповнені в проєкті «запасні» значення (`const GITHUB_TOKEN = '…'`, у GitHub — порожні) переносяться;
  • маніфест (appsscript.json: часовий пояс, права, налаштування вебзастосунку) не чіпаємо;
  • без змін у коді — нічого не робимо; якщо вебзастосунок не знайдено однозначно — код не заливаємо;
  • репозиторій публічний — у лог лише назви файлів і номер версії, без коду, токенів і ID.

    python tools/deploy_apps_script.py            # у GitHub Actions (секрети в env)
    python tools/deploy_apps_script.py --dry-run  # показати, що змінилось би, нічого не заливаючи
"""
import glob
import json
import os
import re
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://script.googleapis.com/v1/projects/"
VER_RE = re.compile(r"^const (VER_\w+) = '([^']+)'", re.M)
EMPTY_RE = re.compile(r"^const (\w+) = '';", re.M)   # «запасне» значення, яке користувач заповнює в Apps Script сам


class DeployError(Exception):
    pass


def credentials(raw: str) -> dict:
    """Вміст ~/.clasprc.json (clasp 3: tokens.default; clasp 2: token + oauth2ClientSettings) → client_id/secret/refresh."""
    d = json.loads(raw)
    t = (d.get("tokens") or {}).get("default")
    if t:
        return {"client_id": t["client_id"], "client_secret": t["client_secret"], "refresh_token": t["refresh_token"]}
    s, t = d.get("oauth2ClientSettings") or {}, d.get("token") or {}
    if s and t.get("refresh_token"):
        return {"client_id": s["clientId"], "client_secret": s["clientSecret"], "refresh_token": t["refresh_token"]}
    raise DeployError("не впізнаю формат CLASPRC_JSON (потрібен вміст ~/.clasprc.json після clasp login)")


def repo_files(root: str = ROOT) -> dict:
    """{'VER_LEDGER': ('ledger', '2026-10-04a', source), ...} з tools/*.gs."""
    out = {}
    for p in sorted(glob.glob(os.path.join(root, "tools", "*.gs"))):
        with open(p, encoding="utf-8") as f:
            src = f.read()
        m = VER_RE.search(src)
        if not m:
            raise DeployError(f"{os.path.basename(p)}: немає константи VER_* — не знаю, якому файлу проєкту відповідає")
        if m.group(1) in out:
            raise DeployError(f"{m.group(1)} у двох файлах репозиторію")
        out[m.group(1)] = (os.path.basename(p)[:-3], m.group(2), src)
    return out


def plan(live: list, repo: dict):
    """Живі файли проєкту + репозиторій → (нові файли для updateContent, [(назва, стара версія, нова версія)], [залишені як є])."""
    files, changed, kept, seen = [], [], [], set()
    if not any(f.get("type") == "JSON" and f.get("name") == "appsscript" for f in live):
        raise DeployError("у проєкті немає маніфесту appsscript.json — щось не так, не заливаю")
    for f in live:
        m = VER_RE.search(f.get("source") or "") if f.get("type") == "SERVER_JS" else None
        if not m or m.group(1) not in repo:
            files.append({k: f.get(k) for k in ("name", "type", "source")})   # без службових полів відповіді GET
            if f.get("type") == "SERVER_JS":
                kept.append(f["name"])
            continue
        if m.group(1) in seen:
            raise DeployError(f"{m.group(1)} у двох файлах проєкту ({f['name']}) — прибери копію вручну")
        seen.add(m.group(1))
        _, ver, src = repo[m.group(1)]
        src, local = keep_local(f["source"], src)
        kept += [f"{f['name']}: {n}" for n in local]
        if src != f["source"]:
            changed.append((f["name"], m.group(2), ver))
        files.append({"name": f["name"], "type": "SERVER_JS", "source": src})
    for key, (name, ver, src) in repo.items():   # новий .gs у репозиторії — новий файл у проєкті
        if key not in seen:
            files.append({"name": name, "type": "SERVER_JS", "source": src})
            changed.append((name, "", ver))
    return files, changed, kept


def keep_local(live: str, src: str):
    """Порожнє в GitHub `const GITHUB_TOKEN = '';`, а в проєкті заповнене — лишаємо значення з проєкту (у лог лише назву)."""
    names = []
    for name in EMPTY_RE.findall(src):
        m = re.search(r"^const " + name + r" = ('[^'\n]+');", live, re.M)
        if m:
            src = re.sub(r"^const " + name + r" = '';", lambda _: f"const {name} = {m.group(1)};", src, count=1, flags=re.M)
            names.append(name)
    return src, names


def web_deployment(deployments: list, manifest: dict) -> dict:
    """Єдине версіоноване розгортання-вебзастосунок (на нього вказує вебхук Telegram)."""
    if not manifest.get("webapp"):
        raise DeployError("у маніфесті немає налаштувань вебзастосунку (webapp) — нова версія могла б вимкнути вебхук; "
                          "онови один раз вручну через «Керування розгортаннями»")
    web = [d for d in deployments if (d.get("deploymentConfig") or {}).get("versionNumber")
           and any(e.get("entryPointType") == "WEB_APP" for e in d.get("entryPoints") or [])]
    if len(web) != 1:
        raise DeployError(f"вебзастосунків із версією: {len(web)} (очікую 1) — залиш одне розгортання "
                          f"в «Керування розгортаннями» → «Архівувати» зайві")
    return web[0]


class Api:
    def __init__(self, creds: dict, script_id: str):
        r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data=dict(creds, grant_type="refresh_token"))
        if r.status_code != 200:
            raise DeployError(f"Google не дав доступ ({r.status_code}: {_err(r)}) — повтори clasp login і онови секрет")
        self.h = {"Authorization": "Bearer " + r.json()["access_token"]}
        self.base = API + script_id

    def call(self, method: str, path: str = "", body=None) -> dict:
        r = requests.request(method, self.base + path, headers=self.h, json=body, timeout=60)
        if r.status_code != 200:
            hint = " — увімкни Apps Script API: script.google.com/home/usersettings" if "has not been used" in r.text \
                or "User has not enabled" in r.text else ""
            raise DeployError(f"{method} {path or '/'} → {r.status_code}: {_err(r)}{hint}")
        return r.json()


def _err(r) -> str:
    try:
        e = r.json().get("error")
        return (e.get("message") if isinstance(e, dict) else str(e or r.text))[:200]
    except ValueError:
        return r.text[:200]


def deploy(api, repo: dict, dry_run: bool = False, log=print) -> list:
    """Повертає список змін [(файл, стара, нова)]; [] — нічого не робили."""
    live = api.call("GET", "/content").get("files") or []
    files, changed, kept = plan(live, repo)
    if kept:
        log("лишаю як є (з проєкту, не з GitHub): " + ", ".join(kept))
    if not changed:
        log("код у проєкті вже такий самий, як у GitHub — нічого не роблю")
        return []
    for name, old, new in changed:
        log(f"оновлюю {name}.gs: {old or 'новий файл'} → {new}")
    manifest = json.loads(next(f["source"] for f in files if f["type"] == "JSON" and f["name"] == "appsscript"))
    dep = web_deployment(api.call("GET", "/deployments").get("deployments") or [], manifest)
    if dry_run:
        log("--dry-run: нічого не заливаю")
        return changed
    api.call("PUT", "/content", {"files": files})
    desc = "GitHub: " + ", ".join(f"{n} {v}" for n, _, v in changed)
    ver = api.call("POST", "/versions", {"description": desc[:100]})["versionNumber"]
    api.call("PUT", "/deployments/" + dep["deploymentId"], {"deploymentConfig": {
        "scriptId": api.base[len(API):], "versionNumber": ver, "manifestFileName": "appsscript", "description": desc[:100]}})
    log(f"✅ залито, версія {ver}, вебзастосунок переведено на неї (адреса та сама)")
    return changed


def notify(text: str):
    token, chat = os.getenv("OFFICE_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                          data={"chat_id": chat, "text": text, "disable_web_page_preview": "true"})
        except requests.RequestException:
            pass


def main():
    dry = "--dry-run" in sys.argv
    raw, sid = os.getenv("CLASPRC_JSON", ""), os.getenv("APPS_SCRIPT_ID", "").strip()
    if not raw or not sid:
        print("секрети CLASPRC_JSON / APPS_SCRIPT_ID не налаштовані — автооновлення вимкнене, оновлюй вручну")
        return 0
    try:
        changed = deploy(Api(credentials(raw), sid), repo_files(), dry)
    except DeployError as e:
        print("⛔ " + str(e))
        if not dry:
            notify("⛔ Автооновлення Apps Script не вдалось: " + str(e))
        return 1
    if changed and not dry:
        notify("🔄 Apps Script оновлено з GitHub: " + ", ".join(f"{n}.gs {v}" for n, _, v in changed) +
               ". Нічого робити не треба.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
