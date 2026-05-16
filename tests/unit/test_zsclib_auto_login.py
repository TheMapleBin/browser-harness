import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "zsclib_auto_login.py"


def load_module():
    spec = importlib.util.spec_from_file_location("zsclib_auto_login", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeHarness:
    def __init__(self, urls, portal_states=None, initial_url=""):
        self.urls = list(urls)
        self.portal_states = list(portal_states or [])
        self.initial_url = initial_url
        self.opened = []
        self.clicked = 0
        self.waits = []

    def new_tab(self, url):
        self.opened.append(url)

    def wait_for_load(self, timeout=15.0):
        self.waits.append(("load", timeout))
        return True

    def page_info(self):
        if self.initial_url is not None:
            url = self.initial_url
            self.initial_url = None
            return {"url": url}
        if not self.urls:
            return {"url": ""}
        return {"url": self.urls.pop(0)}

    def current_tab(self):
        return self.page_info()

    def js(self, expression):
        if "#loginLink" in expression and ".click" in expression:
            self.clicked += 1
            return True
        if self.portal_states:
            return self.portal_states.pop(0)
        return {"has_login_link": True, "captcha_visible": False, "error_text": ""}

    def wait(self, seconds):
        self.waits.append(("sleep", seconds))

    def wait_for_network_idle(self, timeout=10.0, idle_ms=500):
        self.waits.append(("network_idle", timeout, idle_ms))
        return True


class ZSClibAutoLoginTests(unittest.TestCase):
    def test_is_portal_url_uses_exact_host_and_path_prefix(self):
        module = load_module()

        self.assertTrue(module.is_portal_url("http://172.16.20.119/eportal/index.jsp?anything=dynamic"))
        self.assertTrue(module.is_portal_url("https://172.16.20.119/eportal/"))
        self.assertFalse(module.is_portal_url("http://172.16.20.119/other/index.jsp"))
        self.assertFalse(module.is_portal_url("http://example.com/eportal/"))

    def test_already_online_exits_without_clicking_or_opening_new_tabs(self):
        module = load_module()
        harness = FakeHarness([], initial_url="https://www.msn.com/zh-cn?ocid=wispr")

        status = module.run_login_flow(
            harness,
            logger=lambda **_: None,
        )

        self.assertEqual(status, module.STATUS_ALREADY_ONLINE_OR_NO_PORTAL)
        self.assertEqual(harness.opened, [])
        self.assertEqual(harness.clicked, 0)

    def test_clicks_login_from_existing_portal_tab_without_opening_trigger_url(self):
        module = load_module()
        harness = FakeHarness(
            [
                "http://neverssl.com/",
            ],
            [{"has_login_link": True, "captcha_visible": False, "error_text": ""}],
            initial_url="http://172.16.20.119/eportal/index.jsp",
        )

        status = module.run_login_flow(
            harness,
            logger=lambda **_: None,
        )

        self.assertEqual(status, module.STATUS_LOGIN_SUCCESS)
        self.assertEqual(harness.opened, [])
        self.assertEqual(harness.clicked, 1)

    def test_uses_existing_portal_tab_before_opening_trigger_url(self):
        module = load_module()
        harness = FakeHarness(
            ["http://neverssl.com/"],
            [{"has_login_link": True, "captcha_visible": False, "error_text": ""}],
            initial_url="http://172.16.20.119/eportal/index.jsp",
        )

        status = module.run_login_flow(harness, logger=lambda **_: None)

        self.assertEqual(status, module.STATUS_LOGIN_SUCCESS)
        self.assertEqual(harness.opened, [])
        self.assertEqual(harness.clicked, 1)

    def test_portal_success_page_counts_as_already_online(self):
        module = load_module()
        harness = FakeHarness([], initial_url="http://172.16.20.119/eportal/success.jsp?userIndex=abc")

        status = module.run_login_flow(harness, logger=lambda **_: None)

        self.assertEqual(status, module.STATUS_ALREADY_ONLINE_OR_NO_PORTAL)
        self.assertEqual(harness.clicked, 0)

    def test_captcha_visible_stops_before_click(self):
        module = load_module()
        harness = FakeHarness(
            [],
            [{"has_login_link": True, "captcha_visible": True, "error_text": ""}],
            initial_url="http://172.16.20.119/eportal/index.jsp",
        )

        status = module.run_login_flow(harness, logger=lambda **_: None)

        self.assertEqual(status, module.STATUS_CAPTCHA_REQUIRED)
        self.assertEqual(harness.clicked, 0)

    def test_missing_login_button_is_distinct_failure(self):
        module = load_module()
        harness = FakeHarness(
            [],
            [{"has_login_link": False, "captcha_visible": False, "error_text": ""}],
            initial_url="http://172.16.20.119/eportal/index.jsp",
        )

        status = module.run_login_flow(harness, logger=lambda **_: None)

        self.assertEqual(status, module.STATUS_LOGIN_BUTTON_NOT_FOUND)
        self.assertEqual(harness.clicked, 0)

    def test_login_success_when_click_leaves_portal(self):
        module = load_module()
        harness = FakeHarness(
            [
                "http://neverssl.com/",
            ],
            [{"has_login_link": True, "captcha_visible": False, "error_text": ""}],
            initial_url="http://172.16.20.119/eportal/index.jsp",
        )

        status = module.run_login_flow(harness, logger=lambda **_: None)

        self.assertEqual(status, module.STATUS_LOGIN_SUCCESS)
        self.assertEqual(harness.clicked, 1)

    def test_declares_still_on_portal_without_reopening_trigger_url(self):
        module = load_module()
        harness = FakeHarness(
            [
                "http://172.16.20.119/eportal/index.jsp",
            ],
            [{"has_login_link": True, "captcha_visible": False, "error_text": ""}],
            initial_url="http://172.16.20.119/eportal/index.jsp",
        )

        status = module.run_login_flow(harness, logger=lambda **_: None)

        self.assertEqual(status, module.STATUS_STILL_ON_PORTAL_AFTER_CLICK)
        self.assertEqual(harness.opened, [])


if __name__ == "__main__":
    unittest.main()
