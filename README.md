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

> **iSmart 说明**：上报分数要带 native 生成的 `ut` 签名，纯 Python 造不出来。
> 本项目的做法是**让页面自己去发请求**（签名在发送瞬间由页面生成），因此支持两种宿主：
> **iSmart 桌面客户端**或**系统自带 Edge/Chrome**（浏览器模式，无需额外安装）。
> 刷作业走「录制一次流程 → 按任务列表回放」，刷时长走「保持任务页活跃」。
> 页面 DOM 因版本而异，**首次使用需要现场校准**（「🧭 抓取页面结构」+「⏺ 录制流程」）。

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
.\.venv\Scripts\python.exe -m pytest          # 164 个用例，Qt 相关走 offscreen 后端
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

iSmart 适配器是通过 **Chrome DevTools Protocol 驱动一个真实页面**来工作的，宿主有两种：

| 方式 | 需要什么 | 按钮 |
| --- | --- | --- |
| A. 桌面客户端 | 安装 iSmart 桌面版 | **「🚀 启动并连接」** |
| B. 浏览器模式 | **不用装任何东西**，用系统自带的 Edge / Chrome | **「🌐 浏览器模式」** |

**B（浏览器模式）的用法**——这是没有客户端时最省事的路径：

1. 点「🌐 浏览器模式」，填入学校给的 iSmart 网页版地址（会记住，存在 `ismart_profile.json`）；
   官方登录页示例：`https://me.ismartlearning.cn/front/login.html`
2. 工具用**独立的 `user-data-dir`** 启动 Edge/Chrome（不碰你日常浏览器配置），
   并带 `--remote-debugging-port` 打开该地址；
3. **在弹出的浏览器里手动登录**（iSmart 走 CAS，带图形验证码，所以必须人工过一次）；
   登录态存在 `browser-profile/` 里，下次不用重登；
4. 进到**课程列表页**，回本窗口点「🔄 刷新课程」；
5. 若仍提示找不到课程 → 点「🔎 页面自检」，把 `dumps/introspect_*.json` 看一眼（或发我），
   按真实结构改 `courses_js` / `tasks_js`；之后「⏺ 录制流程」录一遍操作即可批量刷。

**A（桌面客户端）**：点「🚀 启动并连接」，工具会在注册表/常见目录里找客户端并带
`--remote-debugging-port` 启动；也可以自己给快捷方式加该参数后启动，再点「🔐 登录」。
端口不是 9222 也没关系，会自动探测 `9222/9223/9224/9229/8315`。

> 连不上时界面会明确告诉你：**已尝试哪些端口**、**有没有找到客户端/浏览器**、**下一步做什么**。
> 默认提交流程是「点任务 → 等 1.2s → 点『提交』 → 等 1.5s → 校验成功标志」，
> 不同版本按钮文案可能不同，改 `submit_steps` 里的 `text` 即可。

### iSmart 刷作业 / 刷时长（推荐流程）

**刷作业**（一次性校准，之后自动跑）：

1. 连上页面（「🚀 启动并连接」或「🌐 浏览器模式」）并**登录**，停在**学生中心 / 课程列表**页
   （如 `me.ismartlearning.cn/center/student/index.html`）；
2. 点「🔄 刷新课程」：工具按「进入课程」按钮识别卡片，列出
   `card:0 周三5-6节（主讲: 徐辉 老师）` 这样的课程；
3. 点 **「⏺ 录制流程」** → 在窗口里手动走一遍（进入课程 → 答题 → 提交）→ 回来点 **「⏹ 结束录制」**；
   录制结果写进 `ismart_profile.json` 的 `recorded_steps`；
4. 回主界面选中账号 → 详情窗选课程、勾单元 → 「▶️ 开始刷作业」。
   工具会**逐张课程卡片**：进课程 → 重放录制流程 → 自动回到列表 → 下一张。

**刷时长**：

1. 模式选「刷时长」，填总时长/并发后开始；
2. 工具逐张卡片进课程，并**保持页面活跃**（每 15 秒一次轻度鼠标/滚动活动），随时可「⏹ 停止」；
3. 时长是否计入由平台判定——本工具负责让页面看起来一直有人在看，具体结算看平台后台。

## 常见问题

- **提示「本机无法解密该账号口令」**：`accounts.json` 被拷到别的机器/用户，DPAPI 解不开，重新添加账号即可。
- **iSmart 连不上**：先看提示。要么装桌面版点「🚀 启动并连接」，要么点「🌐 浏览器模式」填网页版地址。
- **浏览器模式报「未找到 Edge / Chrome」**：装 Edge 或 Chrome 即可（Win10/11 通常自带 Edge）。
- **iSmart 抓不到课程/任务**：确认已登录且停在**课程列表页**；再不行点「🔎 页面自检」——
  它会穿透 iframe / Shadow DOM，导出 data-* 属性统计、按钮文案、输入框与 XHR 接口，
  比整页 HTML 好定位。把 `dumps/introspect_*.json` 发我即可精确改选择器。
- **浏览器模式能看到课程，但提交没反应**：说明网页版页面结构不同（或该页的提交依赖客户端能力），
  用「抓取页面结构」看实际 DOM 再改 `submit_steps`；网页版若没有提交入口，这条路走不通，只能等 `ut` 签名方案。
- **刷时长按钮不见了**：iSmart 不支持刷时长，界面会按平台能力自动隐藏。
- **想看详细日志**：`logs/` 下按日期命名的文件。