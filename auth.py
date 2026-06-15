#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨平台校园网自动认证脚本 (支持 OpenWRT / Generic Linux)
  首次运行引导配置，后续读取 /etc/auth.conf 自动认证。
  配合 crontab 实现断网自动重连，日志 24 小时自动覆盖。
  
  跨平台特性：
    - 自动识别环境：如果是普通 Linux PC，自动跳过并忽略 OpenWRT 特有的 UCI 静态 IP 锁定逻辑，不会报错崩溃。
    - 智能网口探测：通用支持通过路由表、活动接口扫描来锁定 WAN 口。

  用法:
    python3 /root/auth.py              单次检测认证
    python3 /root/auth.py --setup      强制重新配置
    python3 /root/auth.py --restore-dhcp  恢复 DHCP (仅 OpenWRT)
"""

import os, sys, re, json, time, subprocess
import urllib.request, urllib.error

NL          = chr(10)
CONF_FILE   = "/etc/auth.conf"
LOCK_FLAG   = "/tmp/.auth_static_locked"
LOG_FILE    = "/var/log/auth.log"
LOG_MAX_AGE = 86400
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0"

# 全局环境检查：是否为 OpenWRT 环境
IS_OPENWRT  = os.path.exists("/sbin/uci")

# ================================================================
# 配置
# ================================================================
def load_config():
    if not os.path.exists(CONF_FILE):
        return None
    cfg = {}
    try:
        with open(CONF_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
    except:
        return None
    for k in ["USERNAME", "PASSWORD", "AUTH_HOST", "AUTH_PORT"]:
        if k not in cfg or not cfg[k]:
            return None
    return cfg

def save_config(cfg):
    lines = [
        "# 校园网认证配置",
        "",
        "USERNAME={}".format(cfg["USERNAME"]),
        "PASSWORD={}".format(cfg["PASSWORD"]),
        "AUTH_HOST={}".format(cfg["AUTH_HOST"]),
        "AUTH_PORT={}".format(cfg.get("AUTH_PORT", "9090")),
        "AUTH_DOMAIN={}".format(cfg.get("AUTH_DOMAIN", "")),
        "TEST_IP={}".format(cfg.get("TEST_IP", "223.5.5.5")),
        "LOCK_STATIC_IP={}".format(cfg.get("LOCK_STATIC_IP", "NO")),
        "",
    ]
    with open(CONF_FILE, "w") as f:
        f.write(NL.join(lines))
    try:
        os.chmod(CONF_FILE, 0o600)
    except:
        pass

def interactive_setup():
    print("=" * 50)
    print("  校园网自动认证 - 初次配置")
    print("=" * 50)
    print()
    cfg = {}
    cfg["USERNAME"] = input("  学号/用户名: ").strip()
    if not cfg["USERNAME"]:
        print("用户名不能为空，退出。")
        sys.exit(1)
    cfg["PASSWORD"] = input("  密码: ").strip()
    if not cfg["PASSWORD"]:
        print("密码不能为空，退出。")
        sys.exit(1)
    cfg["AUTH_HOST"] = input("  认证网关 IP: ").strip()
    if not cfg["AUTH_HOST"]:
        print("网关地址不能为空，退出。")
        sys.exit(1)
    port = input("  认证网关端口 (默认 9090): ").strip()
    cfg["AUTH_PORT"] = port if port else "9090"
    print()
    print("  [重要] 认证页面在浏览器地址栏显示的域名是什么?")
    print("  例如: xywrz.xxxxx.cn (不带 http:// 和端口)")
    print("  网关通过 HTTP Host 头做虚拟主机路由，填错会导致超时。")
    domain = input("  Portal 域名: ").strip()
    if not domain:
        print("Portal 域名不能为空，认证必然超时，退出。")
        sys.exit(1)
    cfg["AUTH_DOMAIN"] = domain
    tip = input("  连通性检测 IP (默认 223.5.5.5): ").strip()
    cfg["TEST_IP"] = tip if tip else "223.5.5.5"
    
    if IS_OPENWRT:
        lock = input("  是否锁定静态 IP? (y/n, 默认 n): ").strip().upper()
        cfg["LOCK_STATIC_IP"] = "YES" if lock in ("Y", "YES") else "NO"
    else:
        cfg["LOCK_STATIC_IP"] = "NO"
        print("  提示：检测到当前为非 OpenWRT 环境，自动关闭静态 IP 锁定功能。")

    save_config(cfg)
    print()
    print("配置已保存 -> {}".format(CONF_FILE))
    print("如需修改请执行: python3 {} --setup".format(sys.argv[0]))
    print()
    return cfg

# ================================================================
# 日志
# ================================================================
def rotate_log():
    try:
        if os.path.exists(LOG_FILE):
            if time.time() - os.path.getmtime(LOG_FILE) > LOG_MAX_AGE:
                open(LOG_FILE, "w").close()
    except:
        pass

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = "[{}] {}".format(ts, msg)
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + NL)
    except:
        pass

# ================================================================
# 系统工具（兼容性重构）
# ================================================================
def run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", 1
    except:
        return "", "", 1

def get_ip(iface):
    out, _, _ = run("ip addr show {} 2>/dev/null".format(iface))
    if not out:
        return None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("inet ") and not s.startswith("inet6"):
            m = re.search(r"inet\s+([\d.]+)", s)
            if m:
                return m.group(1)
    return None

def get_mask(iface):
    out, _, _ = run("ip addr show {} 2>/dev/null".format(iface))
    if not out:
        return "255.255.255.0"
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("inet "):
            m = re.search(r"inet\s+[\d.]+/(\d+)", s)
            if m:
                cidr = int(m.group(1))
                mask = (0xFFFFFFFF << (32 - cidr)) & 0xFFFFFFFF
                return "{}.{}.{}.{}".format(
                    (mask >> 24) & 0xFF, (mask >> 16) & 0xFF,
                    (mask >> 8) & 0xFF, mask & 0xFF)
    return "255.255.255.0"

def get_gw():
    out, _, _ = run("ip route show default 2>/dev/null | awk '{print $3}'")
    return out or None

def get_dns():
    dns = []
    # 1. 尝试 OpenWRT 的自动 DNS 路径
    if os.path.exists("/tmp/resolv.conf.d/resolv.conf.auto"):
        try:
            with open("/tmp/resolv.conf.d/resolv.conf.auto") as f:
                for line in f:
                    m = re.match(r"nameserver\s+([\d.]+)", line)
                    if m: dns.append(m.group(1))
        except:
            pass
    # 2. 如果没获取到，尝试标准 Linux 的公共 resolv.conf
    if not dns and os.path.exists("/etc/resolv.conf"):
        try:
            with open("/etc/resolv.conf") as f:
                for line in f:
                    m = re.match(r"nameserver\s+([\d.]+)", line)
                    if m and not m.group(1).startswith("127."):
                        dns.append(m.group(1))
        except:
            pass
    return " ".join(dns) if dns else "223.5.5.5"

def detect_wan():
    # 1. 如果是 OpenWRT，优先问 UCI
    if IS_OPENWRT:
        out, _, rc = run("uci get network.wan.ifname 2>/dev/null")
        if rc == 0 and out:
            return out

    # 2. 跨平台通用方案：查内核默认网关出口
    out, _, rc = run("ip route show default 2>/dev/null | awk '{print $5}'")
    if rc == 0 and out:
        return out.split()[0] if out.split() else None

    # 3. 兜底策略：遍历查找最像外网网口的设备（排除局域网和本地回环）
    for iface in ["eth1", "eth0.2", "eth0", "wan", "enp3s0", "wlan0"]:
        ip = get_ip(iface)
        if ip and not ip.startswith("192.168.") and not ip.startswith("127.") and not ip.startswith("172."):
            return iface
            
    # 4. 获取所有物理 link 状态
    out, _, rc = run("ip -o link show 2>/dev/null | awk -F': ' '{print $2}'")
    if rc == 0 and out:
        for n in out.splitlines():
            n = n.strip()
            if n and n not in ["lo", "br-lan", "docker0"] and not n.startswith("veth"):
                return n
    return None

def lock_ip(iface):
    if not IS_OPENWRT:
        return
    if os.path.exists(LOCK_FLAG):
        log("静态 IP 已锁定，跳过")
        return
    ip = get_ip(iface)
    gw = get_gw()
    nm = get_mask(iface)
    dns = get_dns()
    if not ip or not gw:
        log("FAIL  {0} 无 IP/网关，无法锁定".format(iface))
        return
    log("锁定 {0}: ip={1} gw={2} mask={3} dns={4}".format(iface, ip, gw, nm, dns))
    run("cp /etc/config/network /etc/config/network.bak.$(date +%s)")
    run("uci set network.wan.proto='static'")
    run("uci set network.wan.ipaddr='{}'".format(ip))
    run("uci set network.wan.netmask='{}'".format(nm))
    run("uci set network.wan.gateway='{}'".format(gw))
    run("uci delete network.wan.dns")
    for d in dns.split():
        run("uci add_list network.wan.dns='{}'".format(d))
    run("uci commit network")
    run("/etc/init.d/network reload")
    with open(LOCK_FLAG, "w") as f:
        f.write(NL.join([iface, ip, gw, nm, ""]))
    time.sleep(3)
    log("静态 IP 已锁定")

# ================================================================
# 网络核心认证
# ================================================================
def ping_test(ip):
    _, _, rc = run("ping -c 1 -W 2 {} 2>/dev/null".format(ip))
    return rc == 0

def fetch(url, follow_redirect=True):
    if follow_redirect:
        opener = urllib.request.build_opener()
    else:
        class NoRedir(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args):
                return None
        opener = urllib.request.build_opener(NoRedir)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, r.read().decode(errors="replace"), r.url
    except urllib.error.HTTPError as e:
        if 300 <= e.code <= 308:
            loc = e.headers.get("Location", "")
            return e.code, loc, loc
        return e.code, e.read().decode(errors="replace"), url
    except Exception as e:
        return 0, str(e), url

def extract_params(html):
    result = {}
    pat = re.compile(r'<input[^>]*type\s*=\s*["\']hidden["\'][^>]*>', re.I)
    for tag in pat.findall(html):
        n = re.search(r'name\s*=\s*["\']([^"\']+)', tag)
        v = re.search(r'value\s*=\s*["\']([^"\']*)', tag)
        if n:
            result[n.group(1)] = v.group(1) if v else ""
    portal = re.search(r'/portal/login\?([^"\'&\s]+(?:&[^"\'&\s]+)*)', html)
    if portal:
        for part in portal.group(1).split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                if k not in result:
                    result[k] = v
    keys = ["wlanuserip", "nasip", "mac", "url", "wlanacname",
            "nasid", "vid", "port", "nasportid", "apmac", "t", "ssid", "snmpagentip"]
    for key in keys:
        if key not in result:
            m = re.search(r'["\']' + key + r'["\']\s*[:=]\s*["\']([^"\']*)', html)
            if m:
                result[key] = m.group(1)
    return result

def do_login(cfg, params):
    payload = {
        "wlanuserip":   params.get("wlanuserip", ""),
        "nasip":        params.get("nasip", ""),
        "url":          params.get("url", ""),
        "wlanacname":   params.get("wlanacname", ""),
        "ssid":         params.get("ssid", ""),
        "mac":          params.get("mac", ""),
        "username":     cfg["USERNAME"],
        "pwd":          cfg["PASSWORD"],
        "t":            params.get("t", "wireless-v2"),
        "snmpAgentIp":  None,

        "serviceId":    None,
        "validCode":    None,
        "validCodeFlag": False,
        "iarmdst":      None,
        "clientmac":    None,
    }
    for k in ["nasid", "vid", "port", "nasportid", "apmac"]:
        if params.get(k):
            payload[k] = params[k]

    domain = cfg.get("AUTH_DOMAIN", cfg["AUTH_HOST"])
    gw_ip   = cfg["AUTH_HOST"]
    gw_port = cfg["AUTH_PORT"]
    login_url = "http://{0}:{1}/api/loginVue/do".format(gw_ip, gw_port)
    host_hdr  = "{0}:{1}".format(domain, gw_port)
    origin    = "http://{0}:{1}".format(domain, gw_port)
    body      = json.dumps(payload)

    log("  curl POST -> {0}".format(login_url))
    log("  Host: {0}".format(host_hdr))

    cmd = [
        "curl", "-s", "-m", "15",
        "-X", "POST",
        "-H", "Host: {}".format(host_hdr),
        "-H", "User-Agent: {}".format(UA),
        "-H", "Accept: application/json, text/plain, */*",
        "-H", "Accept-Language: zh-CN,zh;q=0.9",
        "-H", "Content-Type: application/json;charset=UTF-8",
        "-H", "Origin: {}".format(origin),
        "-H", "Referer: {}/".format(origin),
        "-H", "Dnt: 1",
        "-H", "Connection: keep-alive",
        "-d", body,
        login_url
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        resp = r.stdout.strip()
        log("  curl rc={0}".format(r.returncode))
        if r.returncode != 0:
            log("  curl stderr: {0}".format(r.stderr.strip()))
            return False
        j = json.loads(resp)
        rc, msg = j.get("code", -1), j.get("msg", "")
        log("  code={0}  msg={1}".format(rc, msg))
        return rc in (200, 1200)
    except subprocess.TimeoutExpired:
        log("  curl 超时")
        return False
    except Exception as e:
        log("  curl 异常: {0}".format(e))
        return False

# ================================================================
# 主流程
# ================================================================
def apply_static_lock(cfg):
    if cfg.get("LOCK_STATIC_IP", "NO").strip().upper() != "YES":
        return
    if not IS_OPENWRT:
        return
    if os.path.exists(LOCK_FLAG):
        return
    iface = detect_wan()
    if not iface:
        log("锁定静态 IP 失败: 未找到 WAN 接口")
        return
    proto, _, _ = run("uci get network.wan.proto 2>/dev/null")
    if proto == "static":
        log("WAN 已是静态 IP，写入标记")
        with open(LOCK_FLAG, "w") as f:
            f.write("already_static" + NL)
        return
    lock_ip(iface)

def run_auth(cfg):
    rotate_log()
    apply_static_lock(cfg)
    test_ip = cfg.get("TEST_IP", "223.5.5.5")
    
    if ping_test(test_ip):
        log("ONLINE  ping {0} 可达".format(test_ip))
        return True
        
    log("OFFLINE  ping {0} 不可达，开始认证".format(test_ip))
    iface = detect_wan()
    if not iface:
        log("ERROR  未找到 WAN 接口")
        return False
    log("WAN={0}  IP={1}".format(iface, get_ip(iface) or "?"))

    auth_url = "http://{0}:{1}/".format(cfg["AUTH_HOST"], cfg["AUTH_PORT"])
    html = ""
    for target in [auth_url, "http://www.baidu.com"]:
        code, body, _ = fetch(target, follow_redirect=False)
        if any(k in body for k in ["xywrz", "loginVue", "portal", "wlanuserip"]):
            html = body
            break
        elif 300 <= code <= 308 and body:
            code2, body2, _ = fetch(body, follow_redirect=True)
            if any(k in body2 for k in ["xywrz", "loginVue", "portal"]):
                html = body2
                break
                
    if not html:
        log("ERROR  未找到认证页面")
        return False

    try:
        with open("/tmp/portal.html", "w") as f:
            f.write(html)
    except:
        pass

    params = extract_params(html)
    if not params.get("wlanuserip") or not params.get("nasip"):
        log("ERROR  缺少关键参数 (wlanuserip/nasip)")
        return False
    log("参数: {0} 项".format(len(params)))

    success = do_login(cfg, params)
    if success:
        time.sleep(3)
        if ping_test(test_ip):
            log("SUCCESS  外网已恢复")
        else:
            log("WARNING  登录返回成功但 ping {0} 仍不通".format(test_ip))
    else:
        log("FAILED  登录失败")
    return success

# ================================================================
# 入口
# ================================================================
def main():
    import argparse
    ap = argparse.ArgumentParser(description="跨平台校园网自动认证脚本 (OpenWRT/Linux PC)")
    ap.add_argument("--setup", action="store_true", help="强制重新配置")
    ap.add_argument("--daemon", type=int, default=0, help="守护模式，每 N 秒（推荐用 crontab）")
    ap.add_argument("--restore-dhcp", action="store_true", help="恢复 WAN 口为 DHCP (仅 OpenWRT 生效)")
    args = ap.parse_args()

    if args.restore_dhcp:
        if not IS_OPENWRT:
            print("当前系统非 OpenWRT，无需恢复 DHCP。")
            sys.exit(0)
        for c in ["uci set network.wan.proto='dhcp'",
                  "uci delete network.wan.ipaddr",
                  "uci delete network.wan.netmask",

                  "uci delete network.wan.gateway",
                  "uci delete network.wan.dns",
                  "uci commit network",
                  "/etc/init.d/network reload"]:
            run(c)
        if os.path.exists(LOCK_FLAG):
            os.remove(LOCK_FLAG)
        log("已恢复 DHCP")
        sys.exit(0)

    if args.setup and os.path.exists(CONF_FILE):
        os.remove(CONF_FILE)

    cfg = load_config()
    if cfg is None:
        cfg = interactive_setup()
    else:
        if not cfg.get("AUTH_DOMAIN", "").strip():
            log("WARNING  AUTH_DOMAIN 为空，强制重新配置")
            if os.path.exists(CONF_FILE):
                os.remove(CONF_FILE)
            cfg = interactive_setup()
        else:
            print("已加载配置: {0}".format(CONF_FILE))

    if args.daemon:
        while True:
            try:
                run_auth(cfg)
            except:
                pass
            time.sleep(args.daemon)
    else:
        ok = run_auth(cfg)
        sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()

