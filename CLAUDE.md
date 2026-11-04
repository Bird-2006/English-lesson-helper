# CLAUDE.md

本文件为 AI 助手（Claude Code / DSH 等）在本仓库工作时的上下文说明。

## 项目概述

**WeLearn 学习助手 V5.0.12**（包名 `auto-welearn`）：Windows 桌面 GUI，通过模拟浏览器会话直接调用
**welearn.sflep.com** 的 HTTP 接口，批量完成课程作业（刷作业）与学习时长（刷时长），支持多账号与断点续跑。

- 技术栈：Python 3.9+ / PyQt5（Widgets，非 QML）/ requests / openpyxl；无 Web 服务、无数据库，状态全在本地 JSON。
- 仓库内不含任何个人署名、联系方式或上游仓库地址（素材与品牌标识均为中性占位）。
- 全部代码为 Windows 专用：`winreg`、`winsound`、`netsh`、`ipconfig`、`DPAPI`、`CREATE_NO_WINDOW`。

## 运行 / 测试 / 打包

```powershell
# 开发环境（仓库内已有 .venv，Python 3.12）
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py

# 测试（52 个用例，Qt 相关走 offscreen 后端；conftest.py 自动配好插件路径）
.\.venv\Scripts\python.exe -m pytest

# 静态检查（规则集见 pyproject.toml 的 [tool.ruff]）
.\.venv\Scripts\python.exe -m ruff check .

# 打包（onedir）
pyinstaller "WeLearn学习助手V5.0.12.spec"
```

已知环境限制：**PyInstaller 构建要求 Python 解释器（含 PyQt5 安装目录）位于纯 ASCII 路径**。
若 `.venv` 本身在中文路径下，Qt 会把路径前缀转成 `?`，报
`Qt plugin directory ... does not exist`。解法：在 ASCII 路径（如 `C:\Temp\wl_venv`）建解释器并安装依赖，
源码也复制到 ASCII 路径（如 `C:\Temp\wl_build`）后构建。源码运行与 pytest 不受此影响。

## 目录结构

```
main.py                    入口：全局 excepthook、拉起主窗口、退出前刷日志
conftest.py                pytest 配置（sys.path + Qt offscreen + 插件路径）
core/
  version.py               版本号唯一来源（APP_VERSION / APP_TITLE / APP_VENDOR）
  paths.py                 app_dir / resource_path / data_path / logs_dir（源码 vs 打包统一入口）
  constants.py             ProgressEvent / AccountStatus / TaskType / ErrCode
  logger.py                WeLearnV4 命名空间 + RotatingFileHandler(5MB×5) + get_logger/shutdown
  atomic.py                write_text_atomic：临时文件 + fsync + os.replace
  secure_store.py          账号口令的 Windows DPAPI 加解密（不可用时退化为明文）
  crypto.py                登录口令编码：base64("T1*hex(password)")
  api.py                   WeLearnClient：唯一出网层
  account_manager.py       Account 数据类 + 账号 CRUD/导入导出/持久化
  task_progress.py         task_progress.json 的读写、完成标记、过期清理
  network_fixer.py         netsh/ipconfig/注册表代理/HOSTS 的诊断与修复
ui/
  helpers.py               消息框工厂、图标、背景图缩放缓存
  main_window.py           WeLearnUI：主窗口/托盘/启动弹窗/退出流程/CHANGELOG 数据
  account_view.py          账号表格、增删、Excel 导入、导出
  account_detail.py        单账号控制台（登录→选课→选单元→跑任务→倒计时）
  workers.py               QThread 子类：Login/Course/Units/Study/TimeStudy
  network_fix_dialog.py    网络工具箱（DiagnosisThread / FixThread）
tests/                     单元测试 + offscreen 冒烟测试
style.qss                  全局样式表（UTF-8，中文注释正常）
version_info.txt           exe 版本资源，由 .spec 的 version= 引用
```

已删除的死代码：`core/batch_manager.py`、`core/reward_data.py`、`reward_base64.txt`、`admin.manifest`
（`uac_admin=True` 已覆盖提权需求），以及 `create_menu_bar` / `show_about` / `open_github` / `on_row_double_clicked`。
此外还移除了署名类素材（图标、背景图）；`ui/helpers.py` 现在按中性的 `app.ico` / `app_bg.png` 查找，
文件不存在时自动退回系统默认样式，不影响运行。

## 数据文件

| 文件 | 位置 | 写入者 |
| --- | --- | --- |
| `accounts.json` | `paths.data_path()`：源码=项目根，打包=exe 同目录（历史文件在 CWD 时优先复用） | `AccountManager.save_accounts()`，原子写 |
| `task_progress.json` | 同上 | `TaskProgress._save_progress()`，原子写 |
| `logs/WeLearnV4_YYYY-MM-DD.log` | `paths.logs_dir()` | `core/logger.py`，DEBUG 级、5MB 轮转 |
| `settings.ini` | `%LOCALAPPDATA%\WeLearnHelper\WeLearn学习助手\` | QSettings：`installation_id`、`dont_show_update_announcement`、`announcement_shown`、`last_version` |

- 口令用 DPAPI 加密落盘（值形如 `dpapi:<base64>`）。换机器/换用户会解不开：`unprotect()` 返回 `None`，
  账号密码置空并记 `_locked_accounts`，界面提示重新输入，**不会崩**。
- `accounts.json` / `task_progress.json` 已写入 `.gitignore`，`.spec` 也不再打包它们。
- `Account` 字段：`username / password / nickname / status / progress / uid`（`uid` 是登录后回填的 WeLearn 数字 ID，
  用于「继续未完成任务」精确匹配）。

## 核心流程

### 1. 登录（`WeLearnClient.login`，最多 3 次重试，网络类错误才重试）

1. `GET /user/prelogin.aspx?loginret=...` → 跟随重定向；
2. `parse_prelogin_url()` 用 `urllib.parse.parse_qs` 取 `code_challenge` / `state`（取不到即 `ErrCode.PARSE`）；
3. `generate_cipher_text(password)` → `[base64("T1*hex(pwd)"), str(T1)]`；
4. `POST https://sso.sflep.com/idsvr/account/login`，表单 `rturl/account/pwd/ts`；`code==1` → `ErrCode.AUTH`（不重试）；
5. 再 `GET` 一次 prelogin，把 WeLearn 侧 cookie 收进 Session。

`WeLearnClient.session` 是**线程局部**的（`threading.local`），并发线程不会互相踩 cookie。

### 2. 课程 → 单元 → SCO

| 步骤 | 接口 |
| --- | --- |
| 课程列表 | `GET /ajax/authCourse.aspx?action=gmc` → `clist[]`（`cid / name / per`） |
| 课程详情 | `GET /student/course_info.aspx?cid=` → 正则 `"uid":(\d+)`、`"classid":"(\w+)"` |
| 单元列表 | `GET /ajax/StudyStat.aspx?action=courseunits` → `info[]` |
| 单元内课程 | `GET /ajax/StudyStat.aspx?action=scoLeaves` → `info[]`（`id / location / isvisible / iscomplete`） |

可见性/完成度仍是**中文字符串匹配**：`isvisible != "false"`、`"未" in iscomplete` 才算未完成。

### 3. 刷作业（`StudyThread`）

- 逐单元取叶子 → 过滤可见且未完成 → `ThreadPoolExecutor(max_concurrent)` 并发；
- 每门课 `submit_course_progress()` 发三次请求（`startsco160928` → `setscoinfo` → `savescoinfo160928`），
  返回 4 个计数；成功判定走 `_ret_ok()`（优先解析 `ret`，非 JSON 时退回子串）；
- CMI 报文由 `build_cmi_payload()` 用 `json.dumps` 生成，尾部拼协议要求的 `[INTERACTIONINFO]`；
- **只重试真正失败的课程**（旧实现整单元重扫），最多 3 轮；
- 完成后弹窗 5 秒倒计时自动切刷时长（`_ask_auto_duration` → `auto_start_time_study`）。

### 4. 刷时长（`TimeStudyThread`）

- `_prepare_all_courses()` 拉所有选中单元的可见课程，摊平成 `(unit_index, chapter, seconds)`；
- `calculate_unit_time()` 用 `divmod` 分配：**各课程秒数之和严格等于单元总秒数**（旧实现整除会丢余数）；
- 每门课 `simulate_time(..., stop_event=...)`：`startsco` → 可中断等待（`Event.wait`）→ 每 60 秒心跳 →
  `savescoinfo(progress=100)`；
- 结果字典含 `completed_units` / `total_units`，界面不再显示「已完成 0/N」。

### 5. 停止与中断（协作式）

`_BaseThread` 持 `threading.Event`；`stop()` / `request_stop()` 只置位，不调用 `QThread.terminate()`。
`simulate_time` 与各并发批次的 `as_completed` 循环都会检查该事件，通常 1 秒内退出；
最坏情况卡在一个 HTTP 请求的 socket 超时内（10~30s），`stop_study()` 会 `wait(5000)` 后放行并记录日志。
`os.kill(threadId)`、`terminate()`、`deleteLater()` 的硬杀路径已全部移除。

### 6. 状态回传与节流

`AccountDetailDialog.update_status()` 只改本地标签，并通过 300ms 单次 `QTimer` 合并后发
`status_updated(username, status, progress)`；主窗口转给 `AccountView.update_account_status()`，
后者**只更新对应行的两个单元格**（旧实现每条消息都「写 JSON + 读 JSON + 整表重建」，刷时长时每秒上百次）。
`AccountManager.update_status(..., save=False)` 供高频路径使用，落盘由任务状态切换与退出时负责。

### 7. 断点续跑

- `start_study()` 写 `task_progress.json`（`status=paused`）；
- 任务正常结束 → `mark_task_completed()`；启动时 `prune()` 清掉已完成/超 7 天的记录；
- 两条续跑入口：主窗口 `check_incomplete_tasks()`（按 `account.uid` 匹配，匹配不到则提示登录后再续）、
  详情窗 `check_incomplete_task()`（登录后按 `uid` 查，`continue_task` → 等单元加载 → `_execute_continue_task`）。

## 日志

- 只有 `WeLearnV4` 及其子 logger 有 handler：`get_logger("StudyThread")` 返回 `WeLearnV4.StudyThread`，
  **会**写进日志文件。旧实现返回的是 root 的子 logger，全项目线程日志一条都没落盘；
  实测修复前后：`logs/*.log` 中 `StudyThread` 命中数 0 → 1（`WeLearnV4.StudyThread - INFO`）。
- 退出路径（`main.py`、`WeLearnUI._shutdown`）都会 `shutdown()` 刷盘。
- `AccountManager.load_accounts()` 只记录用户名，不再把含口令的整个 dict 写进 DEBUG 日志。

## 测试与验证现状

- `pytest`：52 用例全绿。覆盖 `crypto`、`api`（URL 解析 / CMI 报文 / 错误分类）、`task_progress`
  （原子性、完成标记、清理）、`secure_store`（DPAPI 往返、篡改返回 None）、`account_manager`
  （CRUD、表头跳过、导入导出、坏文件容错）、`workers`（时长分配总和、并发钳制、协作停止）、
  `test_ui_smoke.py`（窗口构建、Excel 导入、状态节流、**`_auto_start_tick` 回归**、关闭三分支）。
- `ruff check .`：0 问题（`select = E4/E7/E9/F/I/UP032/B006/B008/B904`，忽略 `E501`）。
- 无界面启动冒烟已通过：`QT_QPA_PLATFORM=offscreen` 下构建主窗口、读入账号、正常退出。
- 打包已实测验证（ASCII 路径下）：产物含 `style.qss`，**不含** `accounts.json`，
  exe 版本资源为 `5.0.12.0 / WeLearn学习助手`，证明 `.spec` 的 `version=` 已生效。
- 本机 `accounts.json` 已完成 DPAPI 迁移（口令字段为 `dpapi:...`，解密往返验证通过）。

## 修改约定

- **语言**：注释、日志、UI 文案一律中文；标识符、接口参数、日志级别英文。UI 文案沿用 emoji 前缀风格
  （`▶️ 开始刷作业`、`🔧 网络工具箱`、`✅ 成功`、`❌ 失败`）。
- **路径**：只通过 `core.paths` 取路径，不要再写 `sys._MEIPASS` 判断（旧代码抄了 4 份）。
- **弹窗**：用 `ui.helpers.message()` 或 `no_help(box)`，保持去掉问号按钮；需要任务栏显示的弹窗不要设 parent。
- **持久化**：一律 `core.atomic.write_text_atomic`，禁止直接 `open(path, "w")` 覆写状态文件。
- **网络层**：异常一律转成 `(False, 错误串)` 并设置 `last_error_code`（`ErrCode`），UI 不解析中文错误串。
- **线程**：新增后台任务继承 `ui.workers._BaseThread`，用 `stop_requested` 做协作取消，禁止 `terminate()`。
- **新依赖**：同步 `pyproject.toml`、`requirements.txt`；若打包需要，再补 `.spec` 的 `hiddenimports`。
- **提交前**：`pytest` + `ruff check .` 全绿；不要提交 `accounts.json`、`task_progress.json`、`logs/`、`dist/`、`build/`、`.venv/`。

## 仍未处理的已知限制

1. 上游契约脆弱：`iscomplete` 靠中文「未」字判断、登录依赖 `client_id=welearn_web` 与 `prelogin` 参数名，
   服务端改版即失效（但至少解析失败会给出明确错误码，不再静默）。
2. Excel 导入只支持 `.xlsx/.xlsm`（`.xls` 需用户另存），这是移除 pandas 的代价。
3. 未做自动更新、i18n、多语言；公告内容仍硬编码在 `ui/main_window.py` 的 `CHANGELOG` 数据里。
4. `QMessageBox` 仍是模态调用，长任务中途的提示会阻塞该窗口自身交互（与原版行为一致）。