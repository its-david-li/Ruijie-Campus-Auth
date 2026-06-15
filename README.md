# 🚀 Ruijie Campus Auth 

<div align="center">

![Python Version](https://img.shields.io/badge/Python-3.11.14-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Platform](https://img.shields.io/badge/Platform-OpenWRT%20%7C%20Linux-orange.svg)

</div>

专为 **无头设备 (Headless Devices)** 设计的锐捷校园网自动认证与断网重连守护脚本。无论是吃灰的 OpenWRT 软路由，还是扔在机房的 Ubuntu/CentOS 服务器，只要能跑 Python，就能让它们永远在线。

> **✅ 测试环境声明**
> 本项目核心逻辑与系统接管功能已在以下环境通过严格测试并稳定运行：
> * **系统环境**：LuCI openwrt-24.10
> * **运行环境**：Python 3.11.14

---

## 📑 目录
- [🎯 核心特性](#-核心特性)
- [📸 运行回显](#-运行回显)
- [🚀 部署与使用指南](#-部署与使用指南)
  - [第一步：抓取接口地址与关键参数](#第一步抓取接口地址与关键参数)
  - [第二步：拉取脚本](#第二步拉取脚本)
  - [第三步：初始化配置与运行](#第三步初始化配置与运行)
  - [第四步：配置自动化守护 (Crontab)](#第四步配置自动化守护-crontab)
- [📂 核心文件路径](#-核心文件路径)
- [🚑 灾难恢复 (仅 OpenWRT)](#-灾难恢复-仅-openwrt)
- [📄 License](#-license)

---

## 🎯 核心特性

在纯 CLI 环境下，校园网 Portal 认证常伴随 curl 超时、动态参数提取困难、以及 DHCP 租期导致的频繁掉线。本项目针对这些痛点进行了底层重构：

* **无头环境原生适配**：专为 OpenWRT 和 Linux CLI 设计，零 GUI 依赖，开箱即用。
* **跨平台环境感知**：
    * **OpenWRT**：自动接管 uci，支持一键锁定静态 IP，从根本上解决 DHCP 租期掉线问题。
    * **通用 Linux**：自动探测默认路由出口并提取网络参数，平滑降级，无依赖报错。
* **极简资源调度**：内建基于 HTTP 204 的轻量级连通性探测。当检测到网络已在线时，脚本会立即安全退出，绝不执行任何冗余发包，极限压榨系统资源。
* **硬核参数抓取**：通过模拟 HTTP 访问触发网关重定向，暴力提取 `wlanuserip`、`nasip` 等核心密钥。
* **规避 SSL/TLS 劫持**：底层调用系统原生 `curl` 派生进程发包，完美绕过低版本 Python `urllib` 带来的证书校验惨案。

[▲ 返回目录](#-目录)

---

## 📸 运行回显

<img width="825" height="419" alt="image" src="https://github.com/user-attachments/assets/1d62af16-6d94-4813-89f5-f2e3c5469318" />



[▲ 返回目录](#-目录)

---

## 🚀 部署与使用指南

为了让脚本顺利接管你的网络，请严格按照以下顺序进行操作。

### 第一步：抓取接口地址与关键参数

在配置脚本前，你必须先在有图形界面的电脑或手机浏览器上抓取目标网关的真实 API 地址。

1. 连接校园网 Wi-Fi 或网线。
2. 打开浏览器，按 `F12` 开启开发者工具，切换到 **Network (网络)** 面板。
3. 勾选 **Preserve log (保留日志)**。
4. 在地址栏访问任意 HTTP 网站（如：`https://baidu.com`）触发 Portal 拦截，并在弹出的页面中完成一次手动登录。
5. 在 **Network** 面板中，找到名为 `do` 或 `loginVue` 的 **POST** 请求。

你需要记录以下两个关键信息，稍后配置会用到：
* **API 真实 IP**：查看该 POST 请求的 `Remote Address`（如：`10.200.203.45:9090`，只取 IP 部分，如：`10.200.203.45`）。
* **认证域名**：查看该请求的 `Host` 头或浏览器地址栏（如：`xywrz.xxxxx.cn`）。

<img width="1920" height="1040" alt="image" src="https://github.com/user-attachments/assets/798b9d80-cd7f-468a-8731-f5189fce9dab" />


---

### 第二步：拉取脚本

SSH 登录到你的 OpenWRT 或 Linux 设备。你可以将脚本下载到任意目录（如：当前用户目录），这里我们直接用相对路径：

~~~bash
wget -O auth.py https://raw.githubusercontent.com/its-david-li/Ruijie-Campus-Auth/main/auth.py
chmod +x auth.py
~~~

---

### 第三步：初始化配置与运行

首次运行必须进入交互式配置向导（需要 `sudo` 权限以写入 `/etc` 目录）：

~~~bash
sudo python3 auth.py --setup
~~~

按提示依次输入：
1. 学号/用户名
2. 密码
3. 认证域名（如第一步获取的 Host：`xywrz.xxxxx.cn`）
4. API 真实 IP（如第一步获取的 Remote Address：`10.200.203.45`）
5. 是否开启静态 IP 锁定（仅 OpenWRT 生效，输入 `y` 开启防掉线保护）

配置完成后，脚本会自动执行一次完整的认证流程。如果日志输出 `SUCCESS` 认证成功，说明网络已打通。

---

### 第四步：配置自动化守护 (Crontab)

为了实现断网自动重连，需要将脚本加入系统的定时任务。得益于脚本的“在线即退出”机制，你可以放心地将其设置为高频检测，完全不用担心系统负载。

输入以下命令编辑定时任务：
~~~bash
crontab -e
~~~

在文件末尾添加以下规则（设置为 **每 2 分钟** 检测一次）：
~~~text
*/2 * * * * /usr/bin/python3 /你的/实际/存放路径/auth.py >/dev/null 2>&1
~~~

> [!WARNING]
> **提示**：Crontab 环境变量中没有当前目录的概念，因此这里必须使用脚本的**绝对路径**。
> * 例如你下载在 `/root` 下就是 `/root/auth.py`
> * 在 `/home/ubuntu` 下就是 `/home/ubuntu/auth.py`

保存退出后，使用以下命令列出当前用户的定时任务，确认是否添加成功：
~~~bash
crontab -l
~~~
确认输出中包含刚刚添加的规则即可。此后，设备将在后台静默守护你的网络连接。

[▲ 返回目录](#-目录)

---

## 📂 核心文件路径

为保证系统整洁，脚本运行过程中涉及的 IO 操作被严格限制在以下两个路径：

| 文件路径 | 用途 | 备注 |
| :--- | :--- | :--- |
| `/etc/auth.conf` | 配置文件 | 存储账号、密码及 API 等信息。纯文本存储，请妥善保护设备权限。 |
| `/tmp/log/auth.log` | 日志文件 | 记录断网重连的详细执行状态。存放于内存 tmp 目录，重启即焚，防止日志无限膨胀撑爆路由器的 Flash 存储。 |

[▲ 返回目录](#-目录)

---

## 🚑 灾难恢复 (仅 OpenWRT)

如果你在 OpenWRT 上开启了“静态 IP 锁定”，且因为配置错误导致路由器彻底失联（如：换了宿舍区导致网段变更），请进入脚本所在目录，通过以下命令一键恢复 WAN 口为 DHCP 模式：

~~~bash
sudo python3 auth.py --restore-dhcp
~~~

[▲ 返回目录](#-目录)

---

## 📄 License

本项目基于 [MIT License](LICENSE) 开源。你可以自由地修改、分发和用于商业用途，但请保留原作者的版权声明。

[▲ 返回目录](#-目录)
