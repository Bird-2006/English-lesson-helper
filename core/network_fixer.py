"""网络诊断与修复工具箱。

改动要点：

- 原来一律 ``shell=True`` 跑字符串命令，参数里的中文/引号容易出问题 →
  改为参数列表直连可执行文件，失败信息更准确；
- 覆盖 HOSTS 前先备份成 ``hosts.bak``，误点「修复」还能救回来；
- ``winreg`` 只在 Windows 上导入，非 Windows 环境不再 import 即崩；
- 删掉了从未被调用的 ``run_cmd_as_admin``（提权由 exe 清单负责）。
"""
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Tuple

from core.logger import get_logger

logger = get_logger("NetworkFixer")

COMMAND_TIMEOUT = 30
HOSTS_PATH = r"C:\Windows\System32\drivers\etc\hosts"
DEFAULT_HOSTS = """# Copyright (c) 1993-2009 Microsoft Corp.
#
# This is a sample HOSTS file used by Microsoft TCP/IP for Windows.
#
# localhost name resolution is handled within DNS itself.
#	127.0.0.1       localhost
#	::1             localhost
"""

if sys.platform == "win32":  # pragma: no cover - 平台分支
    import ctypes
    import winreg
else:  # pragma: no cover - 平台分支
    ctypes = None
    winreg = None


def is_admin() -> bool:
    """是否以管理员身份运行。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_cmd(*args: str) -> Tuple[bool, str]:
    """执行命令，返回 ``(是否执行成功, 合并输出)``。"""
    try:
        completed = subprocess.run(
            list(args),
            check=False,
            shell=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=COMMAND_TIMEOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return True, (completed.stdout or "") + (completed.stderr or "")
    except FileNotFoundError:
        return False, f"找不到命令: {args[0] if args else ''}"
    except subprocess.TimeoutExpired:
        return False, "命令执行超时"
    except Exception as exc:
        return False, str(exc)


class NetworkDiagnostics:
    """网络诊断。"""

    def __init__(self):
        self.results: List[Dict] = []

    def diagnose_all(self) -> List[Dict]:
        self.results = []
        for check in (
            self.check_network_adapter,
            self.check_ip_config,
            self.check_dhcp,
            self.check_dns,
            self.check_hosts_file,
            self.check_lsp,
            self.check_proxy,
            self.check_internet,
        ):
            try:
                check()
            except Exception as exc:  # 单项失败不影响整体诊断
                logger.warning("诊断项 %s 异常: %s", check.__name__, exc)
        return self.results

    def add_result(self, name: str, status: str, detail: str) -> None:
        self.results.append({"name": name, "status": status, "detail": detail})

    # ------------------------------------------------------------ 各项检查

    def check_network_adapter(self) -> None:
        logger.info("检查网络适配器...")
        ok, output = run_cmd("netsh", "interface", "show", "interface")
        if ok and ("已启用" in output or "Enabled" in output):
            self.add_result("网络适配器", "ok", "网络适配器正常")
        elif ok:
            self.add_result("网络适配器", "warning", "可能存在适配器问题")
        else:
            self.add_result("网络适配器", "error", f"无法检测: {output}")

    def check_ip_config(self) -> None:
        logger.info("检查IP配置...")
        ok, output = run_cmd("ipconfig", "/all")
        if not ok:
            self.add_result("IP配置", "error", f"无法检测: {output}")
            return
        if "IPv4 地址" in output or "IPv4 Address" in output:
            if "169.254" in output:
                self.add_result("IP配置", "error", "获得APIPA地址(169.254.x.x)，DHCP可能有问题")
            else:
                self.add_result("IP配置", "ok", "IP配置正常")
        else:
            self.add_result("IP配置", "warning", "未获取到IP地址")

    def check_dhcp(self) -> None:
        logger.info("检查DHCP服务...")
        ok, output = run_cmd("sc", "query", "dhcp")
        if ok and "RUNNING" in output:
            self.add_result("DHCP服务", "ok", "DHCP服务正在运行")
        elif ok:
            self.add_result("DHCP服务", "warning", "DHCP服务未运行")
        else:
            self.add_result("DHCP服务", "warning", "无法检测DHCP服务状态")

    def check_dns(self) -> None:
        logger.info("检查DNS配置...")
        ok, output = run_cmd("ipconfig", "/all")
        if not ok:
            self.add_result("DNS服务", "error", f"无法检测: {output}")
            return
        servers = re.findall(r"DNS 服务器[：:]\s*(\d+\.\d+\.\d+\.\d+)", output)
        if servers:
            self.add_result("DNS服务", "ok", f"DNS服务器: {', '.join(servers)}")
        elif self._internet_ok():
            self.add_result("DNS服务", "ok", "未检测到DNS服务器（可能由VPN/代理接管）")
        else:
            self.add_result("DNS服务", "warning", "未配置DNS服务器")

    def check_hosts_file(self) -> None:
        logger.info("检查HOSTS文件...")
        try:
            with open(HOSTS_PATH, "r", encoding="utf-8", errors="ignore") as stream:
                content = stream.read()
            custom = [line for line in content.splitlines() if line.strip() and not line.startswith("#")]
            if custom:
                self.add_result("HOSTS文件", "warning", f"HOSTS文件有 {len(custom)} 条自定义记录")
            else:
                self.add_result("HOSTS文件", "ok", "HOSTS文件正常")
        except Exception as exc:
            self.add_result("HOSTS文件", "error", f"无法读取HOSTS文件: {exc}")

    def check_lsp(self) -> None:
        logger.info("检查LSP协议...")
        ok, output = run_cmd("netsh", "winsock", "show", "catalog")
        if ok and output.strip():
            self.add_result("LSP协议", "ok", "Winsock目录正常")
        else:
            self.add_result("LSP协议", "warning", "Winsock目录可能有问题")

    def check_proxy(self) -> None:
        logger.info("检查代理配置...")
        if winreg is None:
            self.add_result("代理配置", "ok", "非 Windows 环境，跳过")
            return
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            )
            try:
                proxy_enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            finally:
                winreg.CloseKey(key)
        except OSError:
            self.add_result("代理配置", "ok", "未检测到代理配置")
            return

        if not proxy_enable:
            self.add_result("代理配置", "ok", "系统代理未启用")
        elif self._internet_ok():
            self.add_result("代理配置", "ok", "系统代理已启用（网络正常，可能是VPN/梯子使用中）")
        else:
            self.add_result("代理配置", "warning", "系统代理已启用，可能影响连接")

    def check_internet(self) -> None:
        logger.info("检查互联网连接...")
        if self._internet_ok():
            self.add_result("互联网连接", "ok", "网络连接正常")
        else:
            self.add_result("互联网连接", "error", "无法连接互联网")

    @staticmethod
    def _internet_ok() -> bool:
        ok, output = run_cmd("ping", "-n", "1", "-w", "3000", "baidu.com")
        return ok and "TTL=" in output


class NetworkFixer:
    """网络修复动作。"""

    def reset_winsock(self) -> Tuple[bool, str]:
        logger.info("重置Winsock...")
        ok, output = run_cmd("netsh", "winsock", "reset")
        return (True, "Winsock已重置，需要重启电脑生效") if ok else (False, f"重置失败: {output}")

    def reset_tcp_ip(self) -> Tuple[bool, str]:
        logger.info("重置IP/TCP协议栈...")
        ok, output = run_cmd("netsh", "int", "ip", "reset")
        return (True, "IP/TCP协议栈已重置，需要重启电脑生效") if ok else (False, f"重置失败: {output}")

    def flush_dns(self) -> Tuple[bool, str]:
        logger.info("刷新DNS缓存...")
        ok, output = run_cmd("ipconfig", "/flushdns")
        return (True, "DNS缓存已刷新") if ok else (False, f"刷新失败: {output}")

    def reset_dhcp(self) -> Tuple[bool, str]:
        logger.info("重置DHCP...")
        run_cmd("ipconfig", "/release")
        ok, output = run_cmd("ipconfig", "/renew")
        return (True, "DHCP地址已重新获取") if ok else (False, f"重置失败: {output}")

    def reset_proxy(self) -> Tuple[bool, str]:
        logger.info("重置代理配置...")
        if winreg is None:
            return False, "非 Windows 环境，无法修改代理"
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                0,
                winreg.KEY_SET_VALUE,
            )
            try:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            finally:
                winreg.CloseKey(key)
            return True, "代理配置已重置"
        except Exception as exc:
            return False, f"重置失败: {exc}"

    def fix_hosts(self) -> Tuple[bool, str]:
        """把 HOSTS 恢复为默认内容（先备份原文件）。"""
        logger.info("修复HOSTS文件...")
        backup = HOSTS_PATH + ".bak"
        try:
            if os.path.exists(HOSTS_PATH):
                shutil.copyfile(HOSTS_PATH, backup)
            with open(HOSTS_PATH, "w", encoding="utf-8") as stream:
                stream.write(DEFAULT_HOSTS)
            return True, f"HOSTS文件已恢复默认（原文件已备份到 {os.path.basename(backup)}）"
        except Exception as exc:
            return False, f"修复失败: {exc}"

    def reset_all(self) -> List[Tuple[str, bool, str]]:
        return [
            ("重置Winsock", *self.reset_winsock()),
            ("重置IP/TCP", *self.reset_tcp_ip()),
            ("刷新DNS", *self.flush_dns()),
            ("重置DHCP", *self.reset_dhcp()),
            ("重置代理", *self.reset_proxy()),
            ("修复HOSTS", *self.fix_hosts()),
        ]


__all__ = ["is_admin", "run_cmd", "NetworkDiagnostics", "NetworkFixer", "HOSTS_PATH"]