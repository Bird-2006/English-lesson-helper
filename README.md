# WeLearn 学习助手 V5.0.12

> **免责声明**
>
> - 软件仅供学习参考使用，永久免费，禁止倒卖
> - 禁止用于任何代刷牟利行为
> - 使用本软件产生的任何后果由使用者自行承担
> - 请合理使用，遵守学校相关规定

Windows 桌面工具，通过模拟浏览器会话直接调用 WeLearn（welearn.sflep.com）的 HTTP 接口，批量完成课程作业与学习时长。

## 功能

| 模块 | 说明 |
| --- | --- |
| 刷作业 | 把选中单元里未完成的课程批量标记为已完成，正确率可设固定值或区间随机 |
| 刷时长 | 按单元设定总时长并平均分给该单元每门课，逐秒计时、每 60 秒发心跳，跨单元并发 |
| 多账号 | 添加/删除/Excel 批量导入/导出，每个账号独立窗口与独立会话 |
| 断点续跑 | 任务参数写入 `task_progress.json`，中断后可从主窗口或详情页恢复 |
| 网络工具箱 | 诊断适配器/IP/DHCP/DNS/HOSTS/代理，支持一键修复（需管理员） |

## 环境要求

- Windows 10 / 11（依赖 `winreg`、`winsound`、`netsh`、`ipconfig`）
- Python 3.9 及以上（开发与测试使用 3.12）
- 运行「网络工具箱」的修复动作需要管理员权限；打包产物已内置 `requireAdministrator` 清单

## 安装与运行

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

python main.py
```

## 测试与静态检查

```powershell
.\.venv\Scripts\python.exe -m pytest          # 52 个用例，Qt 相关用例走 offscreen 后端
.\.venv\Scripts\python.exe -m ruff check .    # 规则集见 pyproject.toml
```

## 打包

```powershell
pyinstaller "WeLearn学习助手V5.0.12.spec"
# 产物：dist\WeLearn学习助手V5.0.12\WeLearn学习助手V5.0.12.exe
```

`.spec` 只打包 `style.qss`；**不会**打包 `accounts.json` 等本机私有数据。
如需窗口图标与背景图，在仓库根放 `app.ico` / `app_bg.png`，再把它们加回 `.spec` 的 `datas` 即可（代码里已按这两个文件名查找，缺失时自动退回系统默认样式）。

## 项目结构

```
main.py                     入口：全局异常钩子 + 拉起主窗口 + 退出前刷日志
conftest.py                 pytest 配置（sys.path / Qt offscreen 后端）
core/
  version.py                版本号唯一来源
  paths.py                  源码运行 / PyInstaller 打包的资源与数据路径解析
  constants.py              进度事件、账号状态、任务类型、错误码常量
  logger.py                 日志（WeLearnV4 命名空间 + 轮转文件）
  atomic.py                 原子文件写入（临时文件 + fsync + os.replace）
  secure_store.py           账号口令的 Windows DPAPI 加密存储
  crypto.py                 登录口令编码（base64("T1*hex")）
  api.py                    WeLearn 接口封装（登录/课程/单元/SCO/挂时长）
  account_manager.py        账号 CRUD、导入导出、持久化
  task_progress.py          断点续跑的任务进度存储
  network_fixer.py          网络诊断与修复
ui/
  helpers.py                消息框、图标、背景图缓存
  main_window.py            主窗口、托盘、启动弹窗、退出流程
  account_view.py           账号表格与导入导出
  account_detail.py         单账号控制台（登录→选课→选单元→跑任务）
  workers.py                后台线程（登录/课程/单元/刷作业/刷时长）
  network_fix_dialog.py     网络工具箱对话框
style.qss                   全局样式表（可选的 app.ico / app_bg.png 放仓库根即可生效）
tests/                      单元测试 + 无界面冒烟测试
version_info.txt            exe 版本资源（由 .spec 引用）
```

## 数据文件

| 文件 | 位置 | 说明 |
| --- | --- | --- |
| `accounts.json` | 源码运行=项目根；打包=exe 同目录 | 账号列表，口令用当前 Windows 用户的 DPAPI 加密后保存 |
| `task_progress.json` | 同上 | 未完成任务参数，用于断点续跑 |
| `logs/WeLearnV4_YYYY-MM-DD.log` | 同上 | 运行日志，单文件 5MB 轮转，保留 5 份 |
| `settings.ini` | `%LOCALAPPDATA%\WeLearnHelper\WeLearn学习助手\` | 安装 ID、公告「不再提醒」等界面设置 |

以上两个 JSON 已加入 `.gitignore`，请勿提交或随包分发。

## 使用流程

1. **添加账号**：主界面「➕ 添加账号」，或用「📥 批量导入」选择 Excel（需含 `用户名`/`密码` 列，可选 `昵称`）。
2. **进入详情**：双击账号行，窗口自动登录并拉取课程列表。
3. **选课程 → 选单元**：单元默认全选，可单独勾选。
4. **选模式**：
   - 刷作业：设正确率、并发数；
   - 刷时长：设每单元时长（小时/分钟）、随机扰动、并发数。
5. **开始**：底部显示倒计时与进度，刷作业完成后会提示是否接着刷时长（5 秒后自动开始）。
6. **中断与恢复**：停止或关窗不会丢参数，下次进详情会提示继续未完成任务。

> 并发数不是越大越好：每门课都要占用一个线程等到时长跑完，并发过高只会加剧上游限流与内存占用，默认 8 左右足够。

## 常见问题

- **提示「本机无法解密该账号口令」**：`accounts.json` 被拷到了别的机器/用户下，DPAPI 密文解不开。重新添加账号输入密码即可。
- **网络修复提示需要管理员权限**：右键 exe 选择「以管理员身份运行」，或用打包产物自带的提权清单启动。
- **想看详细日志**：`logs/` 下按日期命名的文件，含每个线程的处理细节。