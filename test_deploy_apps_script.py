"""Тести автооновлення Apps Script (tools/deploy_apps_script.py). Без мережі: фейковий API."""
import json
import sys
import unittest

sys.path.insert(0, "tools")
from deploy_apps_script import DeployError, credentials, deploy, plan, repo_files

MANIFEST = {"name": "appsscript", "type": "JSON",
            "source": json.dumps({"timeZone": "Europe/Berlin", "webapp": {"executeAs": "USER_DEPLOYING", "access": "ANYONE_ANONYMOUS"}})}


def gs(var, ver, body=""):
    return f"// файл\nconst {var} = '{ver}';   // версія\n{body}"


REPO = {"VER_CODE": ("gmail_trigger", "2", gs("VER_CODE", "2", "f()")), "VER_LEDGER": ("ledger", "1", gs("VER_LEDGER", "1"))}
WEB = {"deploymentId": "AKfy-web", "deploymentConfig": {"versionNumber": 7},
       "entryPoints": [{"entryPointType": "WEB_APP"}]}
HEAD = {"deploymentId": "head", "deploymentConfig": {}, "entryPoints": [{"entryPointType": "WEB_APP"}]}


class FakeApi:
    base = "https://script.googleapis.com/v1/projects/SID"

    def __init__(self, live, deployments=(WEB, HEAD)):
        self.live, self.deployments, self.calls = live, list(deployments), []

    def call(self, method, path="", body=None):
        self.calls.append((method, path, body))
        if (method, path) == ("GET", "/content"):
            return {"files": self.live}
        if (method, path) == ("GET", "/deployments"):
            return {"deployments": self.deployments}
        if (method, path) == ("POST", "/versions"):
            return {"versionNumber": 8}
        return {}


class TestDeployAppsScript(unittest.TestCase):
    def live(self):
        return [MANIFEST, {"name": "code", "type": "SERVER_JS", "source": gs("VER_CODE", "1"), "functionSet": {}},
                {"name": "ledger", "type": "SERVER_JS", "source": gs("VER_LEDGER", "1")},
                {"name": "Нотатки", "type": "SERVER_JS", "source": "// без версії"}]

    def test_maps_by_ver_constant_and_keeps_rest(self):
        files, changed, kept = plan(self.live(), REPO)
        self.assertEqual(changed, [("code", "1", "2")])   # code.gs у проєкті = gmail_trigger.gs у репозиторії
        self.assertEqual(kept, ["Нотатки"])
        names = [f["name"] for f in files]
        self.assertEqual(names, ["appsscript", "code", "ledger", "Нотатки"])   # нічого не зникло, імена ті самі
        self.assertEqual(files[1]["source"], REPO["VER_CODE"][2])
        self.assertTrue(all(set(f) == {"name", "type", "source"} for f in files))

    def test_full_deploy_updates_the_web_app(self):
        api, log = FakeApi(self.live()), []
        self.assertEqual(deploy(api, REPO, log=log.append), [("code", "1", "2")])
        methods = [(m, p) for m, p, _ in api.calls]
        self.assertEqual(methods, [("GET", "/content"), ("GET", "/deployments"), ("PUT", "/content"),
                                   ("POST", "/versions"), ("PUT", "/deployments/AKfy-web")])
        cfg = api.calls[-1][2]["deploymentConfig"]
        self.assertEqual((cfg["scriptId"], cfg["versionNumber"], cfg["manifestFileName"]), ("SID", 8, "appsscript"))
        self.assertNotIn("AKfy", " ".join(log))   # ID розгортання (частина адреси вебхука) — не в публічний лог

    def test_nothing_changed_nothing_pushed(self):
        live = self.live()
        live[1]["source"] = REPO["VER_CODE"][2]
        api = FakeApi(live)
        self.assertEqual(deploy(api, REPO, log=lambda s: None), [])
        self.assertEqual([m for m, _, _ in api.calls], ["GET"])

    def test_dry_run_and_unclear_deployment_push_nothing(self):
        api = FakeApi(self.live())
        deploy(api, REPO, dry_run=True, log=lambda s: None)
        self.assertNotIn("PUT", [m for m, _, _ in api.calls])
        for deps in ([HEAD], [WEB, dict(WEB, deploymentId="old")]):
            api = FakeApi(self.live(), deps)
            with self.assertRaises(DeployError):
                deploy(api, REPO, log=lambda s: None)
            self.assertNotIn("PUT", [m for m, _, _ in api.calls])

    def test_manifest_without_webapp_is_refused(self):
        live = self.live()
        live[0] = {"name": "appsscript", "type": "JSON", "source": '{"timeZone": "Europe/Berlin"}'}
        api = FakeApi(live)
        with self.assertRaises(DeployError):
            deploy(api, REPO, log=lambda s: None)
        self.assertNotIn("PUT", [m for m, _, _ in api.calls])

    def test_duplicate_and_new_files(self):
        live = self.live() + [{"name": "code копія", "type": "SERVER_JS", "source": gs("VER_CODE", "0")}]
        with self.assertRaises(DeployError):
            plan(live, REPO)
        repo = dict(REPO, VER_NEW=("newfile", "1", gs("VER_NEW", "1")))
        files, changed, _ = plan(self.live(), repo)
        self.assertIn(("newfile", "", "1"), changed)
        self.assertEqual(files[-1]["name"], "newfile")

    def test_local_values_filled_in_apps_script_survive(self):
        # 04.10: у живому code.gs запасний GITHUB_TOKEN заповнений, у GitHub — порожній; затерти = зупинити картки KA
        repo = {"VER_CODE": ("gmail_trigger", "2", gs("VER_CODE", "2", "const GITHUB_TOKEN = '';   // запасний\nf()"))}
        live = [MANIFEST, {"name": "code", "type": "SERVER_JS",
                           "source": gs("VER_CODE", "1", "const GITHUB_TOKEN = 'secret_x';   // запасний\ng()")}]
        files, changed, kept = plan(live, repo)
        self.assertIn("const GITHUB_TOKEN = 'secret_x';   // запасний\nf()", files[1]["source"])
        self.assertEqual(kept, ["code: GITHUB_TOKEN"])
        self.assertEqual(changed, [("code", "1", "2")])
        live[1]["source"] = gs("VER_CODE", "1", "const GITHUB_TOKEN = '';   // запасний\ng()")
        self.assertIn("const GITHUB_TOKEN = '';", plan(live, repo)[0][1]["source"])   # порожнє лишається порожнім
        log = []
        deploy(FakeApi([MANIFEST, dict(live[1], source=gs("VER_CODE", "1", "const GITHUB_TOKEN = 'secret_x';"))]), repo,
               log=log.append)
        self.assertNotIn("secret_x", " ".join(log))

    def test_real_repo_files_and_credentials(self):
        repo = repo_files()
        self.assertEqual({v[0] for v in repo.values()}, {"gmail_trigger", "ledger", "kafilter", "watchdog"})
        v3 = {"tokens": {"default": {"client_id": "c", "client_secret": "s", "refresh_token": "r", "type": "authorized_user"}}}
        v2 = {"token": {"refresh_token": "r"}, "oauth2ClientSettings": {"clientId": "c", "clientSecret": "s"}}
        for d in (v3, v2):
            self.assertEqual(credentials(json.dumps(d)), {"client_id": "c", "client_secret": "s", "refresh_token": "r"})
        with self.assertRaises(DeployError):
            credentials("{}")


if __name__ == "__main__":
    unittest.main()
