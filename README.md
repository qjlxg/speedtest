来源：https://github.com/faceair/clash-speedtest

https://raw.githubusercontent.com/qjlxg/speedtest/refs/heads/main/clash.yaml

clash-speedtest 用法
-c string

配置文件路径，也支持 http(s) 网址。

-f string

按名称过滤代理节点，使用正则表达式（默认为 .*）。

-b string

按关键词屏蔽代理节点，使用 | 分隔多个关键词（例如：-b 'rate|x1|1x'）。

-server-url string

用于测试代理节点的服务器网址（默认为 https://speed.cloudflare.com）。

-download-size int

测试代理节点的下载文件大小（默认为 50MB）。

-upload-size int

测试代理节点的上传文件大小（默认为 20MB）。

-timeout duration

测试代理节点的超时时间（默认为 5s）。

-concurrent int

下载并发数（默认为 4）。

-output string

输出配置文件的路径（默认为 ""）。

-stash-compatible

启用 Stash 兼容模式。

-max-latency duration

过滤掉延迟大于此值的节点（默认为 800ms）。

-min-download-speed float

过滤掉下载速度小于此值的节点（单位：MB/s）（默认为 5）。

-min-upload-speed float

过滤掉上传速度小于此值的节点（单位：MB/s）（默认为 2）。

-rename

根据 IP 归属地和速度重命名节点。

-fast

启用快速模式，只进行延迟测试。
