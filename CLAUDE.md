# CLAUDE.md

本文件为 AI 助手（Claude Code / DSH 等）在本仓库工作时的上下文说明。

## 项目概述

**English Lesson Helper V6.4.0**（包名 `english-lesson-helper`）：Windows 桌面 GUI，把两个英语网课平台
收敛到同一套界面与任务流程：

| 平台 | 适配器 | 接入方式 | 能力 |
| --- | --- | --- | --- |
| WeLearn（welearn.sflep.com） | `core/platforms/welearn.py` | 账号口令 → 直连 HTTP 接口 | 刷作业 + 刷时长 |
| iSmart（外语智能学习平台） | `core/platforms/ismart.py`（流程引擎 `ismart_flow.py`） | CDP 驱动真实页面（桌面客户端或本机 Edge/Chrome） | 刷作业（录制流程回放）+ 刷时长（保持页面活跃） |

- 技术栈：Python 3.9+ / PyQt5（Widgets）/ requests / openpyxl / websocket-client；状态全在本地 JSON。
- 仓库内不含任何个人署名、联系方式或上游仓库地址。
- 全部代码为 Windows 专用：`winreg`、`winsound`、`netsh`、`ipconfig`、DPAPI、`CREATE_NO_WINDOW`。

## 运行 / 测试 / 打包

```powershell
# 开发环境（仓库内已有 .venv，Python 3.12）
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py

# 测试（251 个用例；Qt 相关走 offscreen，conftest.py 已配好插件路径）
.\.venv\Scripts\python.exe -m pytest

# 静态检查
.\.venv\Scripts\python.exe -m ruff check .

# 打包
pyinstaller EnglishLessonHelper.spec            # 文件夹版 → dist\EnglishLessonHelper\EnglishLessonHelper.exe
pyinstaller EnglishLessonHelper-portable.spec   # 便携单文件 → dist\EnglishLessonHelper-portable.exe（约 40MB）
```

**便携版（onefile）要点**：`uac_admin=False`（双击即开，不弹 UAC；网络修复需手动「以管理员身份运行」），
数据写在 exe 同级目录（`paths.app_dir()` 在 frozen 时返回 exe 目录，而不是 `_MEIPASS` 临时目录），
`style.qss` 仍从 `_MEIPASS` 读取。首次启动因解包会慢几秒。

已知环境限制：**PyInstaller 构建要求 Python 解释器（含 PyQt5 安装目录）位于纯 ASCII 路径**。
中文路径下 Qt 会把前缀转成 `?`，报 `Qt plugin directory ... does not exist`。
解法（无需联网）：把仓库 `.venv` 直接 `robocopy` 到 ASCII 路径（如 `C:\Temp\el_venv`，
拷过去即可用，venv 按可执行文件位置定位），源码也复制到 ASCII 路径（如 `C:\Temp\el_build`，
排除 `.venv` / `logs` / `dist` / `build` / 私有 json），再用 `C:\Temp\el_venv\Scripts\python.exe -m PyInstaller ...` 在那边构建。
源码运行与 pytest 不受此限制影响。

## 目录结构

```
main.py                    入口：全局 excepthook、拉起主窗口、退出前刷日志
conftest.py                pytest 配置（sys.path + Qt offscreen + 插件路径）
core/
  version.py               APP_NAME / APP_NAME_CN / APP_VERSION / APP_TITLE / APP_VENDOR
  paths.py                 app_dir / resource_path / data_path / logs_dir
  constants.py             ProgressEvent / AccountStatus / TaskType / ErrCode
  logger.py                WeLearnV4 命名空间 + RotatingFileHandler(5MB×5)
  atomic.py                write_text_atomic（临时文件 + fsync + os.replace）
  secure_store.py          账号口令的 Windows DPAPI 加解密
  crypto.py                WeLearn 登录口令编码 base64("T1*hex(password)")
  api.py                   兼容层：转出 core.platforms.welearn（旧导入路径仍可用）
  account_manager.py       Account（含 platform 字段）+ CRUD / 导入导出 / 持久化
  task_progress.py         任务进度（记录 platform，续跑按 uid+platform 匹配）
  network_fixer.py         netsh/ipconfig/注册表代理/HOSTS 诊断与修复
  platforms/
    base.py                Capability / PlatformInfo / PlatformClient 抽象
    __init__.py            注册表：CLIENTS / PLATFORMS / create_client / name_from_key
    welearn.py             WeLearnClient（HTTP 直连）
    ismart.py              ISmartClient（CDP）+ ISmartProfile（可配置脚本）
    ismart_flow.py         录制/回放 + 页面自检：build_steps / FlowRunner / INTROSPECT_JS / 保持活跃脚本
ui/
  helpers.py               消息框工厂、图标、背景图缓存
  main_window.py           WeLearnUI：主窗口/托盘/启动声明/退出流程/CHANGELOG
  account_view.py          账号表格（7 列，含平台）、增删、Excel 导入导出
  account_detail.py        单账号控制台，按平台能力渲染控件
  workers.py               QThread 子类：Login/Course/Units/Study/TimeStudy
  network_fix_dialog.py    网络工具箱
tests/                     单元测试 + offscreen 冒烟
style.qss                  全局样式表
version_info.txt           exe 版本资源（6.4.0.0），由 .spec 的 version= 引用
EnglishLessonHelper.spec   PyInstaller 打包脚本（onedir 文件夹版）
EnglishLessonHelper-portable.spec  便携单文件打包脚本（onefile，uac_admin=False）
```

删除过的死代码/素材：`core/batch_manager.py`、`core/reward_data.py`、`reward_base64.txt`、`admin.manifest`、
署名图标与背景图（改为可选 `app.ico` / `app_bg.png`），以及 `create_menu_bar` / `show_about` /
`open_github` / `on_row_double_clicked`。

## 平台契约（新增平台只需实现这些）

`core/platforms/base.py::PlatformClient`：

| 方法 | 语义 |
| --- | --- |
| `login(username, password)` | 建立会话；iSmart 忽略口令、只附着本地客户端 |
| `get_courses()` | 返回 `[{"cid","name","per"}]` |
| `get_course_info(cid)` | 返回 `{"uid","classid","units":[{"id","name",...}]}`；iSmart 的 uid/classid 用占位值 `cdp` |
| `get_sco_leaves(cid, uid, classid, unit_idx)` | 返回 `[{"id","location","isvisible","iscomplete"}]` |
| `submit_course_progress(...)` | 完成一个任务，返回 `(方式1成功, 方式1失败, 方式2成功, 方式2失败)` |
| `simulate_time(..., stop_event=None)` | 挂时长；不支持时返回 `False` 并设 `last_error_code=unsupported` |
| `close()` | 释放会话 |

能力位 `Capability.HOMEWORK / DURATION / CDP` 决定界面显示：两个平台现在都同时具备
刷作业与刷时长；`CDP` 只给 iSmart（决定是否显示「启动并连接 / 浏览器模式 / 抓取页面结构 / 录制流程」）。

新增平台步骤：写一个 `PlatformClient` 子类 → 在 `core/platforms/__init__.py` 的 `CLIENTS` / `PLATFORMS`
注册 → 若需要新的界面控件，用 `client.supports(Capability.X)` 分支。

## WeLearn 流程

1. `login`：`GET /user/prelogin.aspx` → `parse_prelogin_url()` 取 `code_challenge`/`state`
   → `generate_cipher_text` → `POST sso.sflep.com/idsvr/account/login`（`code==1` 判密码错，不重试）
   → 再 GET 一次 prelogin 收 cookie。

   ⚠️ **线程与 cookie 的关系（踩过坑）**：程序里登录在 `LoginThread`、取课程在 `CourseThread`、
   取单元在 `UnitsThread`，`session` 是**每线程一个 Session**（连接池隔离），但 `cookies` 必须是
   **同一个 `RequestsCookieJar` 实例**（`session.cookies = self._cookies`），否则登录态跨线程丢失 →
   取课程返回 HTML 登录页 → `Expecting value: line 1 column 1 (char 0)`。
   回归测试：`tests/test_api.py::test_sessions_are_per_thread_but_share_cookie_jar`。

   ⚠️ **prelogin 的真实返回形态**（踩过坑，别再改回去）：它跳到
   `https://sso.sflep.com/idsvr/transfer.html?returnUrl=%2Fconnect%2Fauthorize%2Fcallback%3F...%26code_challenge%3DXXX%26...%26state%3DYYY`，
   即参数被**二次编码后嵌在 `returnUrl` 里**：分隔符是字面量 `%26`、等号是 `%3D`。
   所以不能用 `parse_qs` 解析顶层 query（取不到），也**不能对值做 unquote**
   （`state` 里带 `%253D`，解码会破坏服务端校验）。`_grab_parameter()` 按参数名匹配、
   原样返回编码片段，且顺序无关；回归测试见
   `tests/test_api.py::test_parse_prelogin_url_handles_nested_encoded_url`。
2. 课程结构：`authCourse.aspx?action=gmc` → `course_info.aspx`（正则抠 uid/classid）→
   `StudyStat.aspx?action=courseunits` → `?action=scoLeaves`。可见/完成判断仍是中文字符串匹配
   （`isvisible != "false"`、`"未" in iscomplete`）。
3. 刷作业：逐单元取叶子 → 过滤 → `ThreadPoolExecutor` 并发 → 每门课 `startsco160928` +
   `setscoinfo`（CMI 报文由 `build_cmi_payload` 生成，尾部拼 `[INTERACTIONINFO]`）+
   `savescoinfo160928` → `_ret_ok()` 判 `ret`。失败项只重试失败的课程，最多 3 轮。
4. 刷时长：`calculate_unit_time()` 用 `divmod` 分配，**各课程秒数之和严格等于单元总秒数**；
   `simulate_time` 用 `Event.wait` 可中断等待，每 60 秒心跳，结束提交 `progress=100`。

## iSmart 流程（CDP）

0. **宿主二选一**（都不需要分发第三方软件）：
   - **桌面客户端**：`find_client_executable()` 查注册表卸载项（DisplayIcon / InstallLocation）与
     `CLIENT_SEARCH_PATTERNS` 通配目录；`launch_client()` 带 `--remote-debugging-port` 启动；
     界面按钮「🚀 启动并连接」。
   - **浏览器模式**：`find_browser()` 找 Edge/Chrome，`launch_browser()` 用**独立 user-data-dir**
     （`<数据目录>/browser-profile`）带调试端口打开 `profile.browser_url`；界面按钮「🌐 浏览器模式」。
     该 URL 存在 `ismart_profile.json`，没有它则提示用户填写。
   两者都走 `LaunchClientThread`（后台线程），最长 25 秒。
1. 用户完成登录 / 打开课程页（桌面客户端或浏览器窗口里）。
2. **附着方式**：`CDPSession.attach_to_browser(port, host_keyword)` 两条路并存，按端点能力自动选：

   | 端点 | 判定 | 附着方式 |
   | --- | --- | --- |
   | 新版 Edge/Chrome | `/json/version` 有 `webSocketDebuggerUrl`，且 `Target.getTargets` 可用 | 浏览器级：`Target.getTargets` 枚举 → `Target.attachToTarget(flatten=True)` 拿 `sessionId` |
   | **iSmart 桌面客户端** | `/json/version` **没有** `webSocketDebuggerUrl`（`Browser` 为空），或 `Target.getTargets` 报 `wasn't found` | **页面级**：`attach_page()` 直接连 `/json/list` 里某个页面的 ws，命令不带 `sessionId` |

   ⚠️ **客户端实测结论（2026-11 现场验证，别再按旧笔记改回浏览器级）**：
   `E:\iSmart\client\iSmart.exe` 内嵌 **Chrome/49.0.2623.110**（2016 年内核），UA 为
   `PC-Up366-iSmart 1.8.0.0 ... Chrome/49.0.2623.110`。它**没有 `Target` 域**，
   浏览器级 ws 也拿不到，只能页面级附着；页面级附着在这套客户端上 `Runtime.evaluate` /
   `Page.enable` 全部可用（已实测读到「我的课程」页 130 字正文）。
   「用 `/json/list` 的 id 连 page WS 收不到回复」那条旧结论只适用于 Edge 的 target 复用场景，
   对 Chrome 49 客户端不成立。

   ⚠️ **老内核语法限制**：Chrome 49 的 `NodeList` **没有** `Symbol.iterator`，
   `[...document.querySelectorAll(...)]` 会抛 `(var)[Symbol.iterator] is not a function`。
   所有注入脚本一律用 `Array.from(...)`（模板字符串、箭头函数、`const`、数组展开本身都支持）。
   `for...of` 也只能用于真数组。
3. `CDPSession` 用 `websocket-client`（`suppress_origin=True`），只用到 `Runtime.evaluate` 与
   `Page.captureScreenshot`；
4. 三个默认 JS（`courses_js` / `units_js` / `tasks_js`）都是**启发式 DOM 扫描**，结果为空时返回
   可操作的中文提示（让你去抓页面结构改配置）；
5. 提交流程：**优先用录制流程**（`profile.recorded_steps`，由「⏺ 录制流程」生成，
   `{scoid}` 占位符按当前任务替换）；没有录制时回落到默认 `submit_steps`
   （`click_task` / `click_text` / `wait_ms` / `eval` / `expect_ok`）；
6. `dump_page()` 导出 HTML + PNG 到 `<数据目录>/dumps/`；`page_introspect()`（「🔎 页面自检」）
   导出 `introspect_*.json`：data-* 属性统计 / 链接 / 按钮 / 输入框 / XHR 接口，
   **穿透 iframe 与 Shadow DOM**，并等异步渲染完成（最多约 5 秒）后再读。
   校准选择器时优先看这个 JSON，不要啃整页 HTML。
7. **录制/回放引擎** `core/platforms/ismart_flow.py`：注入点击监听（capture 阶段）→
   记录 `{t, selector, text, href}` → `build_steps()` 转步骤（相邻间隔转 `wait`，
   `[data-scoid=...]` 占位符化，重复点击合并）→ `FlowRunner.run()` 用 CDP 执行。
   ⚠️ 事件**同时写 localStorage 与内存数组**，并用 `Page.addScriptToEvaluateOnNewDocument`
   在每次导航后自动重新注入：真实平台点一下就会跳页，只存 `window` 会丢（踩过）；
   `stop_recording()` 合并两处来源后清空，并 `removeScriptToEvaluateOnNewDocument`。
   浏览器模式只接受 `http/https`（`normalize_url()`），`data:/file:` 等直接拒绝并清掉旧值。
8. **刷时长**：`simulate_time()` 先点开任务，注入 15 秒一次的轻量鼠标/滚动活动
   （`KEEPALIVE_START_JS`）并 `Page.bringToFront`，`Event.wait` 可中断，结束移除脚本。
   是否计入由平台决定 —— 本工具只负责让页面「看起来一直有人在看」。

⚠️ iSmart 仍未做的部分：`ut` 签名直连（脱离页面自己发请求）。刷作业已经绕开它
（签名由页面在发送瞬间生成），但要做到「纯后台、不开页面」仍需逆向客户端 so。

### 已确认的真实站点结构（`me.ismartlearning.cn`）

- 登录页：`https://me.ismartlearning.cn/front/login.html`，`title=登录`；
- 表单字段：`username`（手机号/邮箱/用户名）、`password`、`captcha`（**验证码**）、
  `remeber`（原文如此）、隐藏域 `lt` / `execution` / `_eventId`
  → 这是 **Apereo CAS** 登录流程，所以**自动登录要过验证码**，不走 OCR 就别尝试代填；
- 另有 `bp-captcha` 与手机验证码输入框；按钮文案「登录」；
- 链接：首页 `http://www.ismartlearning.cn`、忘记密码 `front/index.html?forgotPassword`、
  注册 `front/index.html?regist`；
- 登录后进入**学生中心**：`https://me.ismartlearning.cn/center/student/index.html?studentId=1`
  （`title=学生中心`，顶栏 MyCourse）。课程区结构：
  - 每门课一张卡片：封面图 + 标题（如「周三5-6节」）+ 「主讲: 徐辉 老师」+ 绿色按钮「进入课程」；
  - 右上还有「测验 / 活动 / 问答」三个入口图标，以及「当前课程｜往期课程」切换、「+ 选课」；
  - 下方是 HotCourse 商品区（先声英语学生用书1/2、53元、查看详情）——**不要把这些当成课程**；
  - 卡片**没有 `data-courseid`**，所以 `COURSES_JS` 改为「找『进入课程』按钮 → 向上找卡片取标题」，
    cid 记为 `card:<序号>`（同页内稳定）。
- 因为要验证码，正确姿势是**用户在浏览器窗口里登录一次**，会话存在
  `<数据目录>/browser-profile` 里，下次不用重登；工具只负责登录之后的页面操作。
- **课程列表接口优先**：`get_courses()` 先用页面登录态（CDP `Network.getAllCookies`）调
  `GET https://school.ismartlearning.cn/client/course/list-of-student?status=1&pager.pageSize=50`，
  `extract_list()` 兼容 `data/list/rows/records/items` 等常见包法；失败才回退 DOM 扫描。
  `api_get()` 只做只读 GET，POST 提交仍由页面完成（`ut` 签名在页面里）。
- **抓接口**：`capture_network(seconds)` 走 `Network.enable` + `CDPSession.drain()`，
  聚合 URL（同一 URL 计数）+ 保留 POST body，存 `dumps/api_*.json`；界面按钮「📡 抓接口」。
  这是搞清任务/提交接口（以及 `ut` 长什么样）的唯一可靠入口。
- **卡片级闭环**：`click_course("card:N")` 点开课程并等 URL 变化 →
  执行录制流程/默认提交步骤 → `return_to_list()` 用 `_list_url` 回到列表；
  页面没有单元/任务元素时 `get_course_info`/`get_sco_leaves` 退化为「整门课程」，不再中断。

### 客户端里的真实层级（2026-11 现场逐层实测，全部只读验证过）

```
courses.html                 「我的课程」130 字正文，1 个「进入课程」→ card:0 周三5-6节（徐辉 老师）
  └ course/main.html?courseId=124191&status=1      「课程专题网站-课程主页」
       主页 测试 活动 问答 下载中心 ｜ 章节卡片「新时代明德大学英语综合…」未开始 → [开始学习]
  └ course/bookLearn.html?bookId=F4F84DC6…&courseId=124191   「教材学习」
       章节：UNIT ONE Song of Freedom 0%   [学习]
       章节：UNIT TWO Living with Integrity 0%   [学习]  … 共 8 个单元
  └ 点「学习」→ **弹出新窗口**（页面级 CDP 看不到，必须重新列 /json/list）
       paper.html?chapterId=66BB60…&taskId=66BB60…&paperId=9F468F…&testName=Overview
       title=在线作答；正文「00时00分49秒 已学0 全部0 / Watch the video… / play / 00:00-02:51」
       → 这一层才是「作业/学习」本体：**视频攒时长**，或（有题的单元）选择题作答
```

对应实现：
- `UNITS_FROM_BOOK_JS` / `discover_units()`：教材页章节行（`章节：xxx` + `学习` 按钮）→ `unit:<按钮序号>`
- `CLICK_UNIT_JS`：按序号点第 N 个「学习」
- `click_and_follow(script, "paper.html")`：点完轮询 `/json/list`，发现**新增** target 且 URL 命中 pattern → `_attach_specific()` 附着
- `PLAY_VIDEO_JS` / `READ_PROGRESS_JS` / `play_video(seconds)`：点播放（`video.play()` + `play` 按钮双保险）、
  每 20 秒读一次「已学/全部/当前时间」并补点播放（老内核里视频会自己暂停）
- `study_units(seconds_per_unit)`：遍历章节 → 点学习 → 跟随新窗口 → 播视频，返回 `{done,total,failed}`

⚠️ 客户端是**多窗口**程序：`click_course` 之外的跳转（尤其「学习」）都在新 target 里，
只盯着当前 session 会看到「点了没反应」（实测 URL 不变就是这么来的）。

## 状态回传与断点续跑

- `update_status()` 只改本地标签，300ms 合并后发 `status_updated`；`AccountView.update_account_status()`
  只更新对应行的两个单元格（不再是「写 JSON + 读 JSON + 整表重建」）。
- `AccountManager.update_status(..., save=False)` 供高频路径使用，落盘交给状态切换与退出。
- 任务开始写 `task_progress.json`（`status=paused`，带 `platform`）；正常结束 `mark_task_completed()`；
  启动时 `prune()` 清掉已完成/超 7 天记录；续跑同时匹配 `uid` 与 `platform`。

## 日志

`get_logger(name)` 返回 `WeLearnV4.<name>`，会写进同一天的日志文件（旧实现挂在 root 下，线程日志一条没落盘）。
退出路径（`main.py`、`WeLearnUI._shutdown`）都会 `shutdown()` 刷盘；账号口令从不进日志。

两个 Windows 特有的坑（都已处理，别再退回）：

- **控制台编码**：cp936 终端遇到 emoji（🌐 ⏺）会抛 `UnicodeEncodeError`，logging 于是刷一屏
  `--- Logging error ---`。`_safe_stream()` 会把 stderr `reconfigure(encoding="utf-8", errors="replace")`。
- **轮转被占用**：源码版与便携版同时运行会占用同一个日志文件，`rename` 报 `WinError 32`。
  `_SafeRotatingFileHandler.doRollover()` 捕获后跳过本次轮转，只打一行提示。

## 测试与验证现状

- `pytest`：251 用例全绿。含 `test_platforms.py`（注册表、契约、iSmart 无客户端时的失败路径、
  配置读写、步骤引擎成功/失败/未知步骤、dump_page、core.api 兼容层）、`test_ismart_discovery.py`
  （CDP 端口探测、客户端/浏览器定位、带调试参数启动、超时、错误信息可读性、浏览器模式）、
  `test_ismart_session.py`（sessionId 注入、事件帧跳过、附着重试与放弃）、
  `test_ismart_flow.py`（事件转步骤、占位符替换、回放失败/兜底、录制存盘、挂时长可中断）、
  `test_ismart_recording.py`（地址白名单、导航后重注入的注册/移除、localStorage 合并、截图前切前台）与
  `test_ismart_introspect.py`（登录页识别、渲染重试、自检落盘、提示措辞）、
  `test_ismart_courses.py`（学生中心卡片识别、进/出课程、缺 DOM 时退化、card 模式跳过重复点击）。
  ⚠️ `conftest.py` 的 autouse fixture 会禁用 `CDPSession.attach_to_browser`：
  用户可能正开着浏览器模式的 Edge（9222 且已登录），测试绝不能点到真实页面。
  需要真实浏览器的验证脚本要放在 `tests/` 之外单独跑。
- `ruff check .`：0 问题（`E4/E7/E9/F/I/UP032/B006/B008/B904`，忽略 `E501`）。
- offscreen 冒烟：主窗口构建、7 列表格渲染、WeLearn 账号显示、正常退出。
- 打包实测（ASCII 路径下）：产物含 `style.qss`，不含 `accounts.json`，exe 版本资源生效。
- 便携版实测：`dist\EnglishLessonHelper-portable.exe` 40.42MB；`VersionInfo` 为 6.4.0.0；
  清单为 `asInvoker`（无 UAC）；双击后是 1 个 bootloader + 1 个应用进程，在 **exe 同级** 生成
  `logs/WeLearnV4_*.log`，日志首行为 `English Lesson Helper V6.4.0 启动`。
- 学生中心闭环实测（仿页面 + Edge）：识别到 `[card:0 周三5-6节/主讲徐辉, card:1 周五7-8节]` →
  进课程 → 提交成功 → 自动回到 `/center`；挂时长 3 秒同样回到列表（`CENTER E2E OK`）。
- 真实站点自检实测（Edge + `me.ismartlearning.cn/front/login.html`）：拿到 11 个输入框
  （含 `username`/`password`/`captcha`/`lt`/`execution`/`_eventId`）、按钮「登录」、6 条链接；
  首轮在页面渲染前读会得到全空，故 `page_introspect()` 最多重试 6 次 ×0.8s。
- 跨页录制实测（本地 HTTP 服务 + Edge）：A 页点击 → 跳转 B 页 → B 页点击，录到
  `[click [data-scoid="{scoid}"], wait 2004, click #submit]`，跳转前后都不丢。
- 自动化脚本隔离：`ELH_ISMART_PROFILE` 环境变量可把 iSmart 配置指向临时文件，
  否则 `open_in_browser()/stop_recording()` 里的 `profile.save()` 会写真实配置（踩过一次）。
- 录制/回放/挂时长端到端实测（Edge + 本地页面，3 秒时长）：录制到「点任务 + 点提交」两步 →
  换成任务 s2 回放 → 页面标题变成 `submitted-open-s2` → 挂时长实际耗时 ≥2.5s 且页面仍可用。
- 浏览器模式端到端实测（Edge 156）：带调试端口启动 → 附着 → `document.title` 读取正确 →
  默认 `courses_js` 解析出课程、默认 `tasks_js` 解析出任务 → `dump_page` 落盘 HTML。
- 构建踩坑：仓库里若存在运行中的 `browser-profile/`（浏览器锁文件），`robocopy` 默认无限重试会卡死，
  复制时必须 `robocopy /R:1 /W:1 /XD browser-profile`，该目录也已加入 `.gitignore`。
- 跨线程真实链路验证（三个独立线程）：登录成功 → 4 门课程 → uid=12086092/classid=788917/8 个单元 → 34 个 SCO。
- 本机 `accounts.json` 口令为 DPAPI 密文。

## 修改约定

- **语言**：注释、日志、UI 文案一律中文；标识符/接口参数/日志级别英文。UI 沿用 emoji 前缀风格。
- **平台相关逻辑**：一律写进 `core/platforms/<平台>.py`，界面与 workers 不出现平台分支，
  只用 `client.supports(Capability.X)` 判断。
- **路径**：只通过 `core.paths`；**持久化**：一律 `core.atomic.write_text_atomic`。
- **网络层**：异常转 `(False, 错误串)` 并设置 `last_error_code`，界面不解析中文错误串。
- **线程**：继承 `ui.workers._BaseThread`，用 `stop_requested` 协作取消，禁止 `terminate()`。
- **新依赖**：同步 `pyproject.toml`、`requirements.txt`，打包需要的再补 `.spec` 的 `hiddenimports`。
- **提交前**：`pytest` + `ruff check .` 全绿；不要提交 `accounts.json`、`task_progress.json`、
  `ismart_profile.json`、`dumps/`、`logs/`、`dist/`、`build/`、`.venv/`。

## 仍未处理的已知限制

1. **iSmart 选择器需现场校准**：默认 JS 与默认 `submit_steps` 是启发式默认值，
   首次使用应「抓页面结构」+「⏺ 录制流程」按真实页面生成一次流程。
2. **iSmart 无法脱离页面**：刷作业/刷时长都要求页面开着（签名与计时都依赖页面）；
   要做到纯后台需要逆向客户端 native 签名。
3. WeLearn 上游契约脆弱：`iscomplete` 依赖中文「未」字、登录依赖 `client_id=welearn_web` 与参数名。
4. Excel 导入只支持 `.xlsx/.xlsm`（`.xls` 需另存），这是移除 pandas 的代价。
5. `QMessageBox` 仍是模态调用，任务中途的提示会阻塞该窗口自身交互（沿用原行为）。