# ZSClib Portal Auto Login Portable Package

这是一个可迁移到其他 Windows 电脑使用的 ZSClib 校园网 Portal 自动认证项目副本。

它不保存用户名和密码。用户名、密码、记住密码状态和 Portal Cookie 只保存在专用 Chrome Profile：

```text
C:\BrowserProfiles\ZSClibAutoLogin
```

## 迁移到新电脑

1. 将整个 `browser-harness-zsclib-portable` 文件夹复制到新电脑，例如：

```text
C:\Tools\browser-harness-zsclib-portable
```

2. 安装 Google Chrome。

3. 安装 Python 3.11 或更新版本，并勾选 Add Python to PATH。

4. 在 PowerShell 中进入项目目录并安装本地运行环境：

```powershell
cd "C:\Tools\browser-harness-zsclib-portable"
powershell.exe -ExecutionPolicy Bypass -File ".\windows\setup_zsclib_portable.ps1"
```

5. 连接 WiFi：`ZSClib`。

6. 初始化专用 Chrome Profile：

```powershell
powershell.exe -ExecutionPolicy Bypass -File ".\windows\initialize_zsclib_profile.ps1"
```

在打开的 Chrome 中：

- 进入或等待跳转到认证页
- 输入账号和密码
- 勾选记住密码
- 点击认证页的确定/登录按钮
- 确认网络可用后关闭 Chrome

7. 注册自动认证计划任务：

```powershell
powershell.exe -ExecutionPolicy Bypass -File ".\windows\install_zsclib_task_admin.ps1" -WlanEventDelaySeconds 1 -LogonRetryIntervalMinutes 1 -LogonRetryDurationMinutes 5
```

出现 UAC 时允许管理员权限。管理员窗口显示 `Verified task state:` 后按 Enter 关闭。

## 工作方式

- Windows 连接到 `ZSClib` 后，计划任务延迟 1 秒执行。
- 为了覆盖开机自动连接但 WLAN 事件早于用户登录的情况，登录后计划任务会在 5 分钟内每 1 分钟重试一次。
- 每次 runner 启动后会最多等待 60 秒检测 `ZSClib` 是否已经成为当前 SSID；只有检测到 `ZSClib` 才会继续启动 Chrome。
- PowerShell 以隐藏窗口运行。
- Chrome 以 headless 模式运行，不弹出到屏幕。
- Chrome 只访问一次：

```text
http://www.msftconnecttest.com/redirect
```

- browser-harness 接管这个已打开的页面：
  - 如果当前 URL 是 `172.16.20.119/eportal/success.jsp...`，认为已经在线并退出。
  - 如果当前 URL 是 `172.16.20.119/eportal/...` 且存在 `#loginLink`，点击确定。
  - 如果当前 URL 不是 Portal，认为已经联网或未被 Portal 拦截并退出。

## 手动测试

```powershell
cd "C:\Tools\browser-harness-zsclib-portable"
powershell.exe -ExecutionPolicy Bypass -File ".\windows\run_zsclib_auto_login.ps1"
Get-Content ".\logs\zsclib_auto_login.log" -Tail 80
```

正常日志应包含：

```text
runner version: 2026-05-16-msftconnecttest-v3
trigger: http://www.msftconnecttest.com/redirect
starting Chrome: ... --headless=new ... http://www.msftconnecttest.com/redirect
initial_current_url
```

不应再出现：

```text
portal precheck
neverssl
open_trigger_url
```

## 卸载

删除计划任务：

```powershell
Unregister-ScheduledTask -TaskName "ZSClib Portal Auto Login" -Confirm:$false
```

删除专用 Chrome Profile：

```powershell
Remove-Item -LiteralPath "C:\BrowserProfiles\ZSClibAutoLogin" -Recurse -Force
```

删除项目目录即可移除本项目。删除 Chrome Profile 会清除保存的 Portal 账号密码状态。

## 可调参数

默认参数适用于当前 ZSClib 场景：

```text
SSID: ZSClib
Chrome Profile: C:\BrowserProfiles\ZSClibAutoLogin
CDP URL: http://127.0.0.1:9222
Trigger URL: http://www.msftconnecttest.com/redirect
Task delay after WLAN event: 1 second
Logon retry window: every 1 minute for 5 minutes
Runner waits for ZSClib SSID: up to 60 seconds
```

如果需要改 Profile 路径或端口，可以在运行脚本时传参，例如：

```powershell
powershell.exe -ExecutionPolicy Bypass -File ".\windows\run_zsclib_auto_login.ps1" -ChromeProfile "D:\BrowserProfiles\ZSClibAutoLogin" -CdpPort 9223
```
