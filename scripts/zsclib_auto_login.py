import json
import sys
import time
import traceback
from datetime import datetime, timezone
from urllib.parse import urlparse


TARGET_PORTAL_HOST = "172.16.20.119"
TARGET_PORTAL_PATH_PREFIX = "/eportal/"
TARGET_PORTAL_SUCCESS_PATH = "/eportal/success.jsp"
TRIGGER_URLS = [
    "http://www.msftconnecttest.com/redirect",
]

LOAD_TIMEOUT_SECONDS = 20.0
REDIRECT_SETTLE_SECONDS = 4.0
AFTER_CLICK_WAIT_SECONDS = 5.0
NETWORK_IDLE_TIMEOUT_SECONDS = 6.0

STATUS_ALREADY_ONLINE_OR_NO_PORTAL = "ALREADY_ONLINE_OR_NO_PORTAL"
STATUS_LOGIN_SUCCESS = "LOGIN_SUCCESS"
STATUS_CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
STATUS_LOGIN_BUTTON_NOT_FOUND = "LOGIN_BUTTON_NOT_FOUND"
STATUS_STILL_ON_PORTAL_AFTER_CLICK = "STILL_ON_PORTAL_AFTER_CLICK"
STATUS_BROWSER_HARNESS_ERROR = "BROWSER_HARNESS_ERROR"
STATUS_UNEXPECTED_PAGE = "UNEXPECTED_PAGE"

EXIT_CODES = {
    STATUS_ALREADY_ONLINE_OR_NO_PORTAL: 0,
    STATUS_LOGIN_SUCCESS: 0,
    STATUS_CAPTCHA_REQUIRED: 2,
    STATUS_LOGIN_BUTTON_NOT_FOUND: 1,
    STATUS_STILL_ON_PORTAL_AFTER_CLICK: 1,
    STATUS_BROWSER_HARNESS_ERROR: 3,
    STATUS_UNEXPECTED_PAGE: 1,
}

PORTAL_STATE_JS = r"""
(() => {
  const loginLink = document.querySelector('#loginLink');
  const validCode = document.querySelector('#isDisplayValidCode');
  let captchaVisible = false;
  if (validCode) {
    const style = getComputedStyle(validCode);
    const rect = validCode.getBoundingClientRect();
    captchaVisible =
      style.display !== 'none' &&
      style.visibility !== 'hidden' &&
      style.opacity !== '0' &&
      rect.width > 0 &&
      rect.height > 0;
  }
  const errorNode = document.querySelector('#error_span_content');
  return {
    has_login_link: !!loginLink,
    captcha_visible: captchaVisible,
    error_text: errorNode ? (errorNode.innerText || '').trim() : '',
    url: location.href
  };
})()
"""

CLICK_LOGIN_JS = r"""
(() => {
  const loginLink = document.querySelector('#loginLink');
  if (!loginLink) return false;
  loginLink.click();
  return true;
})()
"""


def is_portal_url(url):
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return False
    return (
        parsed.hostname == TARGET_PORTAL_HOST
        and (parsed.path or "").startswith(TARGET_PORTAL_PATH_PREFIX)
    )


def is_portal_success_url(url):
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return False
    return (
        parsed.hostname == TARGET_PORTAL_HOST
        and (parsed.path or "").lower() == TARGET_PORTAL_SUCCESS_PATH
    )


def status_exit_code(status):
    return EXIT_CODES.get(status, 1)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def log_json(**fields):
    fields.setdefault("ts", utc_now())
    print(json.dumps(fields, ensure_ascii=True, sort_keys=True), flush=True)


def normalize_portal_state(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        value = {}
    return {
        "has_login_link": bool(value.get("has_login_link")),
        "captcha_visible": bool(value.get("captcha_visible")),
        "error_text": str(value.get("error_text") or ""),
        "url": str(value.get("url") or ""),
    }


def current_url(harness):
    current_tab = getattr(harness, "current_tab", None)
    if callable(current_tab):
        info = current_tab()
    else:
        info = harness.page_info()
    if not isinstance(info, dict):
        return ""
    return str(info.get("url") or "")


def read_portal_state(harness):
    return normalize_portal_state(harness.js(PORTAL_STATE_JS))


def click_login_link(harness):
    return bool(harness.js(CLICK_LOGIN_JS))


def wait_after_navigation(harness):
    wait = getattr(harness, "wait", None)
    if callable(wait):
        wait(REDIRECT_SETTLE_SECONDS)
    else:
        time.sleep(REDIRECT_SETTLE_SECONDS)


def wait_after_click(harness, logger):
    wait = getattr(harness, "wait", None)
    if callable(wait):
        wait(AFTER_CLICK_WAIT_SECONDS)
    else:
        time.sleep(AFTER_CLICK_WAIT_SECONDS)
    harness.wait_for_load(timeout=LOAD_TIMEOUT_SECONDS)
    wait_for_network_idle = getattr(harness, "wait_for_network_idle", None)
    if callable(wait_for_network_idle):
        try:
            wait_for_network_idle(
                timeout=NETWORK_IDLE_TIMEOUT_SECONDS,
                idle_ms=700,
            )
        except Exception as exc:
            logger(event="network_idle_wait_failed", error=repr(exc))


def finish(status, logger, **fields):
    logger(event="finish", status=status, exit_code=status_exit_code(status), **fields)
    return status


def run_login_flow(harness, trigger_urls=None, logger=log_json):
    trigger_urls = list(trigger_urls or TRIGGER_URLS)
    if not trigger_urls:
        return finish(STATUS_UNEXPECTED_PAGE, logger, reason="no_trigger_urls")

    trigger_url = trigger_urls[0]
    url = ""
    logger(event="start", trigger_url=trigger_url)

    try:
        wait_after_navigation(harness)
        url = current_url(harness)
        logger(event="initial_current_url", url=url)
    except Exception as exc:
        logger(event="initial_current_url_failed", error=repr(exc))
        url = ""

    if not is_portal_url(url):
        if not url:
            return finish(STATUS_UNEXPECTED_PAGE, logger, reason="empty_current_url")
        return finish(STATUS_ALREADY_ONLINE_OR_NO_PORTAL, logger, url=url)

    if is_portal_success_url(url):
        return finish(STATUS_ALREADY_ONLINE_OR_NO_PORTAL, logger, url=url)

    state = read_portal_state(harness)
    logger(event="portal_state", **state)

    if state["captcha_visible"]:
        return finish(
            STATUS_CAPTCHA_REQUIRED,
            logger,
            url=url,
            error_text=state["error_text"],
        )

    if not state["has_login_link"]:
        return finish(
            STATUS_LOGIN_BUTTON_NOT_FOUND,
            logger,
            url=url,
            error_text=state["error_text"],
        )

    if not click_login_link(harness):
        return finish(
            STATUS_LOGIN_BUTTON_NOT_FOUND,
            logger,
            url=url,
            error_text=state["error_text"],
        )

    logger(event="clicked_login_link", selector="#loginLink")
    wait_after_click(harness, logger)

    after_click_url = current_url(harness)
    logger(event="after_click_url", url=after_click_url)
    if after_click_url and not is_portal_url(after_click_url):
        return finish(STATUS_LOGIN_SUCCESS, logger, url=after_click_url)
    if is_portal_success_url(after_click_url):
        return finish(STATUS_LOGIN_SUCCESS, logger, url=after_click_url)

    return finish(
        STATUS_STILL_ON_PORTAL_AFTER_CLICK,
        logger,
        url=after_click_url or url,
        error_text=state["error_text"],
    )


class BrowserHarnessAdapter:
    def __init__(self, namespace):
        self.namespace = namespace

    def helper(self, name):
        func = self.namespace.get(name)
        if not callable(func):
            raise RuntimeError("browser-harness helper not available: %s" % name)
        return func

    def new_tab(self, url):
        return self.helper("new_tab")(url)

    def wait_for_load(self, timeout=15.0):
        return self.helper("wait_for_load")(timeout=timeout)

    def page_info(self):
        return self.helper("page_info")()

    def current_tab(self):
        return self.helper("current_tab")()

    def js(self, expression):
        return self.helper("js")(expression)

    def wait(self, seconds):
        return self.helper("wait")(seconds)

    def wait_for_network_idle(self, timeout=10.0, idle_ms=500):
        return self.helper("wait_for_network_idle")(timeout=timeout, idle_ms=idle_ms)


def main(namespace=None):
    namespace = namespace if namespace is not None else globals()
    try:
        status = run_login_flow(BrowserHarnessAdapter(namespace), logger=log_json)
    except Exception as exc:
        log_json(
            event="exception",
            status=STATUS_BROWSER_HARNESS_ERROR,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        print("STATUS=%s" % STATUS_BROWSER_HARNESS_ERROR, flush=True)
        return status_exit_code(STATUS_BROWSER_HARNESS_ERROR)

    print("STATUS=%s" % status, flush=True)
    return status_exit_code(status)


def running_under_browser_harness():
    return __name__ == "browser_harness.run" and callable(globals().get("new_tab"))


if __name__ == "__main__" or running_under_browser_harness():
    sys.exit(main())
