from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ZSClibWindowsScriptsTests(unittest.TestCase):
    def test_task_script_registers_wlan_connect_event_for_target_ssid(self):
        text = (ROOT / "windows" / "create_zsclib_task.ps1").read_text(encoding="utf-8")

        self.assertIn("Microsoft-Windows-WLAN-AutoConfig/Operational", text)
        self.assertIn("EventID=8001", text)
        self.assertIn("Data[@Name='SSID']", text)
        self.assertIn("$TargetSsid", text)

    def test_runner_clears_ssl_key_log_file_for_harness_child_process(self):
        text = (ROOT / "windows" / "run_zsclib_auto_login.ps1").read_text(encoding="utf-8")

        self.assertIn('EnvironmentVariables["SSLKEYLOGFILE"]', text)
        self.assertIn('EnvironmentVariables["BU_CDP_URL"]', text)

    def test_runner_does_not_precheck_before_starting_chrome(self):
        text = (ROOT / "windows" / "run_zsclib_auto_login.ps1").read_text(encoding="utf-8")

        self.assertNotIn("function Test-PortalNeeded", text)
        self.assertNotIn("Invoke-CurlProbe", text)
        self.assertIn("Start-DedicatedChrome -InitialUrl $TriggerUrl", text)

    def test_runner_closes_dedicated_chrome_after_success(self):
        text = (ROOT / "windows" / "run_zsclib_auto_login.ps1").read_text(encoding="utf-8")

        self.assertIn("function Stop-DedicatedChrome", text)
        self.assertIn("Stop-DedicatedChrome", text[text.index("Invoke-BrowserHarnessScript"):])
        self.assertNotIn("leaving dedicated Chrome open for inspection", text)

    def test_runner_starts_headless_chrome_with_single_trigger_url(self):
        text = (ROOT / "windows" / "run_zsclib_auto_login.ps1").read_text(encoding="utf-8")

        self.assertIn('$TriggerUrl = "http://www.msftconnecttest.com/redirect"', text)
        self.assertIn("--headless=new", text)
        self.assertIn("Start-DedicatedChrome -InitialUrl $TriggerUrl", text)

    def test_task_uses_one_second_wlan_delay_and_hidden_window(self):
        text = (ROOT / "windows" / "create_zsclib_task.ps1").read_text(encoding="utf-8")

        self.assertIn("[int]$WlanEventDelaySeconds = 1", text)
        self.assertIn("-WindowStyle Hidden", text)

    def test_runner_uses_msftconnecttest_only(self):
        text = (ROOT / "windows" / "run_zsclib_auto_login.ps1").read_text(encoding="utf-8")

        self.assertIn("http://www.msftconnecttest.com/redirect", text)
        self.assertNotIn("neverssl", text.lower())
        self.assertNotIn("curl.exe", text)

    def test_runner_logs_version_and_probe_url(self):
        text = (ROOT / "windows" / "run_zsclib_auto_login.ps1").read_text(encoding="utf-8")

        self.assertIn("$RunnerVersion", text)
        self.assertIn("runner version:", text)
        self.assertIn("trigger:", text)

    def test_runner_skips_harness_reload_by_default(self):
        text = (ROOT / "windows" / "run_zsclib_auto_login.ps1").read_text(encoding="utf-8")

        self.assertIn("[switch]$ReloadHarnessDaemon", text)
        self.assertIn("skipping browser-harness daemon reload", text)

    def test_runner_has_wlan_event_fallback_for_ssid_detection(self):
        text = (ROOT / "windows" / "run_zsclib_auto_login.ps1").read_text(encoding="utf-8")

        self.assertIn("function Get-RecentConnectedSsidFromEventLog", text)
        self.assertIn("Get-WinEvent", text)
        self.assertIn("SsidEventFallbackMaxAgeSeconds", text)
        self.assertIn("SSID from recent WLAN event fallback", text)

    def test_task_script_verifies_task_after_registration(self):
        text = (ROOT / "windows" / "create_zsclib_task.ps1").read_text(encoding="utf-8")

        self.assertIn("Register-ScheduledTask -TaskName $TaskName -Xml $taskXml -Force -ErrorAction Stop", text)
        self.assertIn("function Invoke-SchtasksCreate", text)
        self.assertIn("System.Diagnostics.ProcessStartInfo", text)
        self.assertIn("Get-ScheduledTask -TaskName $TaskName", text)
        self.assertIn("Verified task state", text)
        self.assertIn("Task registration did not produce", text)

    def test_admin_installer_keeps_elevated_window_open(self):
        text = (ROOT / "windows" / "install_zsclib_task_admin.ps1").read_text(encoding="utf-8")

        self.assertIn("[int]$WlanEventDelaySeconds = 1", text)
        self.assertIn("-Verb RunAs", text)
        self.assertIn("-NoExit", text)
        self.assertIn("ReadLine", text)

    def test_portable_setup_and_profile_initializer_exist(self):
        self.assertTrue((ROOT / "README.md").exists())
        self.assertFalse((ROOT / "README_ZSCLIB_PORTABLE.md").exists())
        self.assertTrue((ROOT / "windows" / "setup_zsclib_portable.ps1").exists())
        self.assertTrue((ROOT / "windows" / "initialize_zsclib_profile.ps1").exists())

    def test_profile_initializer_uses_same_profile_and_trigger_url(self):
        text = (ROOT / "windows" / "initialize_zsclib_profile.ps1").read_text(encoding="utf-8")

        self.assertIn("C:\\BrowserProfiles\\ZSClibAutoLogin", text)
        self.assertIn("http://www.msftconnecttest.com/redirect", text)
        self.assertIn("--user-data-dir=$ChromeProfile", text)
        self.assertIn("--remote-debugging-port=$CdpPort", text)

    def test_portable_setup_creates_venv_and_runs_tests(self):
        text = (ROOT / "windows" / "setup_zsclib_portable.ps1").read_text(encoding="utf-8")

        self.assertIn(".venv", text)
        self.assertIn('"-m", "venv"', text)
        self.assertIn('"-m", "pip"', text)
        self.assertIn("install", text)
        self.assertIn("-e", text)
        self.assertIn("unittest", text)

    def test_portable_readme_documents_migration_flow(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("setup_zsclib_portable.ps1", text)
        self.assertIn("initialize_zsclib_profile.ps1", text)
        self.assertIn("install_zsclib_task_admin.ps1", text)
        self.assertIn("C:\\BrowserProfiles\\ZSClibAutoLogin", text)
        self.assertIn("不保存用户名和密码", text)


if __name__ == "__main__":
    unittest.main()
