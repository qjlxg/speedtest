

```markdown
# Clash Speedtest 使用指南

这是一个基于 [faceair/clash-speedtest](https://github.com/faceair/clash-speedtest) 的使用指南，旨在帮助你快速筛选出最快的 Clash 代理节点。

---

## 导入客户端配置

你可以使用以下 URL 导入 Clash 客户端配置：

```

[https://raw.githubusercontent.com/qjlxg/speedtest/refs/heads/main/clash.yaml](https://raw.githubusercontent.com/qjlxg/speedtest/refs/heads/main/clash.yaml)

````

---

## clash-speedtest 用法

### 基础用法

`clash-speedtest` 是一个命令行工具，通过配置不同的参数来实现对 Clash 代理节点的筛选和测速。

```bash
clash-speedtest [flags]
````

### 参数说明

| 参数 | 类型 | 描述 | 默认值 |
| :--- | :--- | :--- | :--- |
| `-c` | `string` | **配置文件路径**，也支持 `http(s)` 网址。 | 无 |
| `-f` | `string` | 按名称**过滤**代理节点，支持正则表达式。 | `.*` |
| `-b` | `string` | 按关键词**屏蔽**代理节点，使用 `\|` 分隔多个关键词。 | 无 |
| `-server-url` | `string` | 用于测试代理节点的**服务器网址**。 | `https://speed.cloudflare.com` |
| `-download-size` | `int` | 测试代理节点的**下载文件大小**（单位：MB）。 | `50MB` |
| `-upload-size` | `int` | 测试代理节点的**上传文件大小**（单位：MB）。 | `20MB` |
| `-timeout` | `duration` | 测试代理节点的**超时时间**。 | `5s` |
| `-concurrent` | `int` | **下载并发数**。 | `4` |
| `-output` | `string` | **输出配置文件**的路径。 | `""` |
| `-stash-compatible` | 无 | **启用 Stash 兼容模式**。 | 无 |
| `-max-latency` | `duration` | 过滤掉**延迟大于**此值的节点。 | `800ms` |
| `-min-download-speed` | `float` | 过滤掉**下载速度小于**此值的节点（单位：MB/s）。 | `5MB/s` |
| `-min-upload-speed` | `float` | 过滤掉**上传速度小于**此值的节点（单位：MB/s）。 | `2MB/s` |
| `-rename` | 无 | 根据 IP 归属地和速度**重命名**节点。 | 无 |
| `-fast` | 无 | **启用快速模式**，只进行延迟测试。 | 无 |

-----

### 示例

**1. 筛选并测速**

```bash
# 从指定的 URL 获取配置文件，并根据下载和上传速度进行筛选
clash-speedtest -c [https://raw.githubusercontent.com/qjlxg/speedtest/refs/heads/main/clash.yaml](https://raw.githubusercontent.com/qjlxg/speedtest/refs/heads/main/clash.yaml) -min-download-speed 10 -min-upload-speed 5
```

**2. 仅进行延迟测试 (快速模式)**

```bash
# 只进行延迟测试，并过滤掉延迟大于 500ms 的节点
clash-speedtest -c my_clash_config.yaml -fast -max-latency 500ms
```

**3. 屏蔽特定节点并重命名**

```bash
# 屏蔽名称中包含 "rate" 或 "x1" 的节点，并根据速度重命名
clash-speedtest -c my_clash_config.yaml -b 'rate\|x1' -rename
```

-----

## 许可证

该项目基于 [faceair/clash-speedtest](https://github.com/faceair/clash-speedtest) 仓库，遵循其相应的开源许可证。

```
```
