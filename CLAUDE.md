# CLAUDE.md

本文件为 AI 助手（Claude Code / DSH 等）在本仓库工作时的上下文说明。

## 项目概述

**English Lesson Helper V6.0.0**（包名 `english-lesson-helper`）：Windows 桌面 GUI，把两个英语网课平台
收敛到同一套界面与任务流程：

| 平台 | 适配器 | 接入方式 | 能力 |
| --- | --- | --- | --- |
| WeLearn（welearn.sflep.com） | `core/platforms/welearn.py` | 账号口令 → 直连 HTTP 接口 | 刷作业 + 刷时长 |
| iSmart（外语智能学习平台） | `core/platforms/ismart.py` | Chrome DevTools Protocol 驱动本地客户端页面 | 仅刷作业（刷时长需 `ut` 签名，未实现） |

- 技术栈：Python 3.9+ / PyQt5（Widgets）/ requests / openpyxl / websocket-client；状态全在本地 JSON。
- 仓库内不含任何个人署名、联系方式或上游仓库地址。
- 全部代码为 Windows 专用：`winreg`、`winsound`、`netsh`、`ipconfig`、DPAPI、`CREATE_NO_WINDOW`。

## 运行 / 测试 / 打包

```powershell
# 开发环境（仓库内已有 .venv，Python 3.12）
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py

# 测试（75 个用例；Qt 相关走 offscreen，conftest.py 已配好插件路径）
.\.venv\Scripts\python.exe -m pytest

# 静态检查
.\.venv\Scripts\python.exe -m ruff check .

# 打包（onedir）
pyinstaller EnglishLessonHelper.spec
```

已知环境限制：**PyInstaller 构建要求 Python 解释器（含 PyQt5 安装目录）位于纯 ASCII 路径**。
中文路径下 Qt 会把前缀转成 `?`，报 `Qt plugin directory ... does not exist`。解法：在 ASCII 路径
（如 `C:\Temp\el_venv`）建解释器并安装依赖，源码也复制到 ASCII 路径后构建。源码运行与 pytest 不受影响。

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
ui/
  helpers.py               消息框工厂、图标、背景图缓存
  main_window.py           WeLearnUI：主窗口/托盘/启动声明/退出流程/CHANGELOG
  account_view.py          账号表格（7 列，含平台）、增删、Excel 导入导出
  account_detail.py        单账号控制台，按平台能力渲染控件
  workers.py               QThread 子类：Login/Course/Units/Study/TimeStudy
  network_fix_dialog.py    网络工具箱
tests/                     单元测试 + offscreen 冒烟
style.qss                  全局样式表
version_info.txt           exe 版本资源（6.0.0.0），由 .spec 的 version= 引用
EnglishLessonHelper.spec   PyInstaller 打包脚本
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

能力位 `Capability.HOMEWORK / DURATION / CDP` 决定界面显示：iSmart 没有 `DURATION`，详情窗不会出现
「刷时长」模式，也不会自动衔接刷时长。

新增平台步骤：写一个 `PlatformClient` 子类 → 在 `core/platforms/__init__.py` 的 `CLIENTS` / `PLATFORMS`
注册 → 若需要新的界面控件，用 `client.supports(Capability.X)` 分支。

## WeLearn 流程

1. `login`：`GET /user/prelogin.aspx` → `parse_prelogin_url()` 用 `parse_qs` 取 `code_challenge`/`state`
   → `generate_cipher_text` → `POST sso.sflep.com/idsvr/account/login`（`code==1` 判密码错，不重试）
   → 再 GET 一次 prelogin 收 cookie。会话是**线程局部**的（`threading.local`），并发不踩 cookie。
2. 课程结构：`authCourse.aspx?action=gmc` → `course_info.aspx`（正则抠 uid/classid）→
   `StudyStat.aspx?action=courseunits` → `?action=scoLeaves`。可见/完成判断仍是中文字符串匹配
   （`isvisible != "false"`、`"未" in iscomplete`）。
3. 刷作业：逐单元取叶子 → 过滤 → `ThreadPoolExecutor` 并发 → 每门课 `startsco160928` +
   `setscoinfo`（CMI 报文由 `build_cmi_payload` 生成，尾部拼 `[INTERACTIONINFO]`）+
   `savescoinfo160928` → `_ret_ok()` 判 `ret`。失败项只重试失败的课程，最多 3 轮。
4. 刷时长：`calculate_unit_time()` 用 `divmod` 分配，**各课程秒数之和严格等于单元总秒数**；
   `simulate_time` 用 `Event.wait` 可中断等待，每 60 秒心跳，结束提交 `progress=100`。

## iSmart 流程（CDP）

1. 用户在客户端快捷方式加 `--remote-debugging-port=9222` 并登录；
2. `discover_pages()` 走 `http://127.0.0.1:9222/json/list`，按 `host_keyword`（默认 `ismart`）挑页面；
3. `CDPSession` 用 `websocket-client` 连 `webSocketDebuggerUrl`，只用到 `Runtime.evaluate` 与
   `Page.captureScreenshot`；
4. 三个默认 JS（`courses_js` / `units_js` / `tasks_js`）都是**启发式 DOM 扫描**，结果为空时返回
   可操作的中文提示（让你去抓页面结构改配置）；
5. 提交流程是 `submit_steps` 列表，支持 `click_task`（按 `scoid`） / `click_text`（按按钮文案） /
   `wait_ms` / `eval` / `expect_ok`；任一步失败即整门课记失败；
6. `dump_page()` 导出 HTML + PNG 到 `<数据目录>/dumps/`，是校准选择器的唯一入口。

⚠️ iSmart 未实现的部分：`ut` 签名直连（社区做法是 unidbg/Frida 还原 so，或复刻签名服务），
以及刷时长。要补这两块，先逆向客户端 so，再把签名接到 `ISmartClient` 之上，不要凭空编造接口。

## 状态回传与断点续跑

- `update_status()` 只改本地标签，300ms 合并后发 `status_updated`；`AccountView.update_account_status()`
  只更新对应行的两个单元格（不再是「写 JSON + 读 JSON + 整表重建」）。
- `AccountManager.update_status(..., save=False)` 供高频路径使用，落盘交给状态切换与退出。
- 任务开始写 `task_progress.json`（`status=paused`，带 `platform`）；正常结束 `mark_task_completed()`；
  启动时 `prune()` 清掉已完成/超 7 天记录；续跑同时匹配 `uid` 与 `platform`。

## 日志

`get_logger(name)` 返回 `WeLearnV4.<name>`，会写进同一天的日志文件（旧实现挂在 root 下，线程日志一条没落盘）。
退出路径（`main.py`、`WeLearnUI._shutdown`）都会 `shutdown()` 刷盘；账号口令从不进日志。

## 测试与验证现状

- `pytest`：75 用例全绿。含 `test_platforms.py`（注册表、契约、iSmart 无客户端时的失败路径、
  配置读写、步骤引擎成功/失败/未知步骤、dump_page、core.api 兼容层）。
- `ruff check .`：0 问题（`E4/E7/E9/F/I/UP032/B006/B008/B904`，忽略 `E501`）。
- offscreen 冒烟：主窗口构建、7 列表格渲染、WeLearn 账号显示、正常退出。
- 打包实测（ASCII 路径下）：产物含 `style.qss`，不含 `accounts.json`，exe 版本资源生效。
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

1. **iSmart 选择器需现场校准**：默认 JS 是启发式扫描，作者无法在无客户端环境下验证真实 DOM，
   首次使用必须抓页面结构后调整 `ismart_profile.json`。
2. **iSmart 无刷时长**：需要 `ut` 签名 + 直连接口，属于下一步逆向工作。
3. WeLearn 上游契约脆弱：`iscomplete` 依赖中文「未」字、登录依赖 `client_id=welearn_web` 与参数名。
4. Excel 导入只支持 `.xlsx/.xlsm`（`.xls` 需另存），这是移除 pandas 的代价。
5. `QMessageBox` 仍是模态调用，任务中途的提示会阻塞该窗口自身交互（沿用原行为）。