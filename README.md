# English Lesson Helper

> **免责声明**
>
> - 软件仅供学习交流使用，永久免费，禁止倒卖
> - 禁止用于任何代刷牟利行为
> - 使用本软件产生的任何后果由使用者自行承担
> - 请合理使用，遵守学校相关规定

面向英语网课的双平台桌面助手：**WeLearn**（welearn.sflep.com）与 **iSmart**（外语智能学习平台）。
一套界面、一套账号管理、一套任务进度，按账号选择平台即可。

## 平台能力

| 能力 | WeLearn | iSmart |
| --- | --- | --- |
| 登录方式 | 账号口令直连接口 | 复用本地客户端登录态（CDP 附着） |
| 刷作业 | ✅ 批量提交学习记录，正确率可配 | ✅ 按脚本步骤操作页面完成任务 |
| 刷时长 | ✅ 逐秒计时 + 每分钟心跳 | ❌ 需要 `ut` 签名直连接口，暂未实现 |
| 断点续跑 | ✅ | ✅ |
| 依赖 | 无（纯 HTTP） | iSmart 客户端 + 启动参数 `--remote-debugging-port=9222` |

> **iSmart 说明**：该平台上报分数要带 native 生成的 `ut` 签名，纯 Python 拿不到。
> 本项目不发明接口，而是用 Chrome DevTools Protocol 驱动官方客户端页面（签名由客户端自己算）。
> 客户端各版本 DOM 不同，代码里的页面脚本是启发式默认值，**首次使用需要现场校准**
> （详情窗里有「🧭 抓取页面结构」按钮，导出 HTML + 截图后改 `ismart_profile.json`）。

## 环境要求

- Windows 10 / 11（依赖 `winreg`、`winsound`、`netsh`、`ipconfig`、DPAPI）
- Python 3.9 及以上（开发与测试使用 3.12）
- 「网络工具箱」的修复动作需要管理员权限；打包产物已内置 `requireAdministrator` 清单

## 安装与运行

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

python main.py
```

## 测试与静态检查

```powershell
.\.venv\Scripts\python.exe -m pytest          # 93 个用例，Qt 相关走 offscreen 后端
.\.venv\Scripts\python.exe -m ruff check .    # 规则集见 pyproject.toml
```

## 打包

### 便携版（单文件，双击即开）

```powershell
pyinstaller EnglishLessonHelper-portable.spec
# 产物：dist\EnglishLessonHelper-portable.exe（约 40 MB）
```

- **只产出一个 exe**，拷到任意文件夹/U 盘即可运行，不需要安装
- 不弹 UAC；需要「网络工具箱」的修复动作时，右键「以管理员身份运行」
- 数据写在 **exe 同级目录**：`accounts.json`、`task_progress.json`、`ismart_profile.json`、`logs/`
- 端口令由当前 Windows 用户的 DPAPI 加密，换机器/换用户需要重新输入密码
- 首次启动稍慢（onefile 会先解包到临时目录），之后正常

### 文件夹版（onedir，启动更快）

```powershell
pyinstaller EnglishLessonHelper.spec
# 产物：dist\EnglishLessonHelper\EnglishLessonHelper.exe
```

两个 `.spec` 都只打包 `style.qss`，**不会**打包 `accounts.json`、`task_progress.json` 等本机数据。
需要窗口图标与背景图时，在仓库根放 `app.ico` / `app_bg.png`，再把它们加回 `.spec` 的 `datas`。

## 项目结构

```
main.py                     入口：全局异常钩子 + 拉起主窗口 + 退出前刷日志
conftest.py                 pytest 配置（sys.path / Qt offscreen）
core/
  version.py                版本与产品标识唯一来源
  paths.py                  源码运行 / PyInstaller 打包的资源与数据路径解析
  constants.py              进度事件、账号状态、任务类型、错误码
  logger.py                 WeLearnV4 命名空间 + 轮转日志
  atomic.py                 原子文件写入
  secure_store.py           账号口令的 Windows DPAPI 加密
  crypto.py                 WeLearn 登录口令编码
  account_manager.py        账号 CRUD / 导入导出 / 持久化（含平台字段）
  task_progress.py          断点续跑的任务进度存储（按平台区分）
  network_fixer.py          网络诊断与修复
  api.py                    兼容层：转出 core.platforms.welearn
  platforms/
    base.py                 平台接口 PlatformClient + 能力标记
    __init__.py             平台注册表（welearn / ismart）
    welearn.py              WeLearn 适配器（HTTP 直连）
    ismart.py               iSmart 适配器（CDP 驱动 + 可配置脚本）
ui/
  helpers.py                消息框工厂、图标、背景图缓存
  main_window.py            主窗口、托盘、启动声明、退出流程
  account_view.py           账号表格（含平台列）、增删、Excel 导入导出
  account_detail.py         单账号控制台，按平台能力渲染控件
  workers.py                后台线程（登录/课程/单元/刷作业/刷时长）
  network_fix_dialog.py     网络工具箱
tests/                      单元测试 + offscreen 冒烟测试
style.qss                   全局样式表
version_info.txt            exe 版本资源（由 .spec 引用）
EnglishLessonHelper.spec          文件夹版打包脚本
EnglishLessonHelper-portable.spec 便携单文件打包脚本
```

## 数据文件

| 文件 | 位置 | 说明 |
| --- | --- | --- |
| `accounts.json` | 源码=项目根；打包=exe 同目录 | 账号列表，口令 DPAPI 加密，带 `platform` 字段 |
| `task_progress.json` | 同上 | 未完成任务参数，按 `uid + platform` 匹配续跑 |
| `ismart_profile.json` | 同上 | iSmart 页面脚本与提交流程，首次运行自动生成，可手工调整 |
| `dumps/` | 同上 | 「抓取页面结构」导出的 HTML / 截图 |
| `logs/WeLearnV4_YYYY-MM-DD.log` | 同上 | 运行日志，5MB 轮转保留 5 份 |
| `settings.ini` | `%LOCALAPPDATA%\EnglishLessonHelper\English Lesson Helper\` | 安装 ID、公告「不再提醒」等 |

## 使用流程

### 通用

1. 「➕ 添加账号」→ 选平台 → 填账号口令（iSmart 可随便填，实际以客户端登录态为准）。
2. 双击账号行进入详情窗 → 自动登录（iSmart 是附着客户端页面）。
3. 选课程 → 勾选单元 → 选模式与参数 → 开始。

### iSmart 首次使用

> **前提**：iSmart 平台必须安装**桌面客户端**（本工具用 CDP 驱动它）。只有网页版时用不了该平台。

1. 详情窗点 **「🚀 启动并连接」**：工具会自动在注册表/常见安装目录里找客户端，
   带 `--remote-debugging-port=9222` 启动并等待端口就绪（最长 25 秒）。
   - 也可以自己给快捷方式加 `--remote-debugging-port=9222` 后启动，再点「🔐 登录」；
   - 客户端端口不是 9222 也没关系，会自动探测 `9222/9223/9224/9229/8315`。
2. 在客户端里打开到课程 / 任务列表页。
3. 点 **「🧭 抓取页面结构」**，在 `dumps/` 里对照 HTML 调整 `ismart_profile.json` 中的
   `courses_js` / `units_js` / `tasks_js` / `submit_steps`。
4. 点「刷新课程」，能列出课程即可开始刷作业。

> 连不上时界面会明确告诉你：**已尝试哪些端口**、**有没有找到客户端**、**下一步做什么**。
> 默认提交流程是「点任务 → 等 1.2s → 点『提交』 → 等 1.5s → 校验成功标志」，
> 不同版本客户端按钮文案可能不同，改 `submit_steps` 里的 `text` 即可。

## 常见问题

- **提示「本机无法解密该账号口令」**：`accounts.json` 被拷到别的机器/用户，DPAPI 解不开，重新添加账号即可。
- **iSmart 提示连不上 / 未找到客户端**：先确认装的是桌面版；装好后点「🚀 启动并连接」。
  若提示「已尝试端口…未在本机找到 iSmart 桌面客户端」，说明注册表与常见目录都没找到，
  请手动从快捷方式启动客户端（加调试参数）后再点「🔐 登录」。
- **iSmart 抓不到课程/任务**：页面没停在对应列表页，或选择器不适配当前版本 —— 抓页面结构后改配置。
- **刷时长按钮不见了**：iSmart 不支持刷时长，界面会按平台能力自动隐藏。
- **想看详细日志**：`logs/` 下按日期命名的文件。