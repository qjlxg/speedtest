# -*- coding: utf-8 -*-
# !/usr/bin/env python3

import base64
import subprocess
import threading
import time
import urllib.parse
import json
import glob
import re
import yaml
import random
import string
import httpx
import asyncio
from itertools import chain
from typing import Dict, List, Optional
import sys
import requests
import zipfile
import gzip
import shutil
import platform
import os
from datetime import datetime
from asyncio import Semaphore
import ssl
import logging
import concurrent.futures
import statistics
from geoip2.database import Reader as GeoIPReader
from playwright.async_api import async_playwright
import socket

ssl._create_default_https_context = ssl._create_unverified_context
import warnings
from concurrent.futures import ThreadPoolExecutor

warnings.filterwarnings('ignore')
import psutil

# Configuration
TEST_URL = "https://www.instagram.com"
SECONDARY_TEST_URL = "https://www.youtube.com"
CLASH_API_PORTS = [9090]
CLASH_API_HOST = "127.0.0.1"
CLASH_API_SECRET = ""
TIMEOUT = 3
SPEED_TEST = True
SPEED_TEST_URL = "http://speed.cloudflare.com/__down?bytes=52428800"
SPEED_TEST_LIMIT = 968
results_speed = []
MAX_CONCURRENT_TESTS = 120
LIMIT = 10000
CONFIG_FILE = 'clash_config.yaml'
INPUT = "input"
BAN = ["中国", "China", "CN", "电信", "移动", "联通", "Hong Kong", "Taiwan", "HK", "TW", "澳门", "Macao", "MO"]
headers = {
    'Accept-Charset': 'utf-8',
    'Accept': 'text/html,application/x-yaml,*/*',
    'User-Agent': 'Clash Verge/1.7.7'
}
STABILITY_TESTS = 3
STABILITY_INTERVAL = 2
MIN_SUCCESS_RATE = 0.8
MAX_STD_DEV = 200
GEOIP_DB_PATH = "GeoLite2-Country.mmdb"
switch_lock = threading.Lock()

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

clash_config_template = {
    "port": 7890,
    "socks-port": 7891,
    "redir-port": 7892,
    "allow-lan": True,
    "mode": "rule",
    "log-level": "info",
    "external-controller": "127.0.0.1:9090",
    "geodata-mode": True,
    'geox-url': {
        'geoip': 'https://raw.githubusercontent.com/Loyalsoldier/geoip/release/geoip.dat',
        'mmdb': 'https://raw.githubusercontent.com/Loyalsoldier/geoip/release/GeoLite2-Country.mmdb'
    },
    "dns": {
        "enable": True,
        "ipv6": False,
        "default-nameserver": [
            "223.5.5.5",
            "119.29.29.29"
        ],
        "enhanced-mode": "fake-ip",
        "fake-ip-range": "198.18.0.1/16",
        "use-hosts": True,
        "nameserver": [
            "https://doh.pub/dns-query",
            "https://dns.alidns.com/dns-query"
        ],
        "fallback": [
            "https://doh.dns.sb/dns-query",
            "https://dns.cloudflare.com/dns-query",
            "https://dns.twnic.tw/dns-query",
            "tls://8.8.4.4:853"
        ],
        "fallback-filter": {
            "geoip": True,
            "ipcidr": [
                "240.0.0.0/4",
                "0.0.0.0/32"
            ]
        }
    },
    "proxies": [],
    "proxy-groups": [
        {
            "name": "节点选择",
            "type": "select",
            "proxies": [
                "自动选择",
                "故障转移",
                "DIRECT",
                "手动选择"
            ]
        },
        {
            "name": "自动选择",
            "type": "url-test",
            "exclude-filter": "(?i)中国|China|CN|电信|移动|联通",
            "proxies": [],
            "url": "http://www.pinterest.com",
            "interval": 300,
            "tolerance": 50
        },
        {
            "name": "故障转移",
            "type": "fallback",
            "exclude-filter": "(?i)中国|China|CN|电信|移动|联通",
            "proxies": [],
            "url": "http://www.gstatic.com/generate_204",
            "interval": 300
        },
        {
            "name": "手动选择",
            "type": "select",
            "proxies": []
        },
    ],
    "rules": [
        "MATCH,节点选择"
    ]
}

def parse_hysteria2_link(link):
    link = link[14:]
    parts = link.split('@')
    uuid = parts[0]
    server_info = parts[1].split('?')
    server = server_info[0].split(':')[0]
    port = int(server_info[0].split(':')[1].split('/')[0].strip())
    query_params = urllib.parse.parse_qs(server_info[1] if len(server_info) > 1 else '')
    insecure = '1' in query_params.get('insecure', ['0'])
    sni = query_params.get('sni', [''])[0]
    name = urllib.parse.unquote(link.split('#')[-1].strip())
    return {
        "name": f"{name}",
        "server": server,
        "port": port,
        "type": "hysteria2",
        "password": uuid,
        "auth": uuid,
        "sni": sni,
        "skip-cert-verify": not insecure,
        "client-fingerprint": "chrome"
    }

def parse_ss_link(link):
    link = link[5:]
    if "#" in link:
        config_part, name = link.split('#')
    else:
        config_part, name = link, ""
    decoded = base64.urlsafe_b64decode(config_part.split('@')[0] + '=' * (-len(config_part.split('@')[0]) % 4)).decode('utf-8')
    method_passwd = decoded.split(':')
    cipher, password = method_passwd if len(method_passwd) == 2 else (method_passwd[0], "")
    server_info = config_part.split('@')[1]
    server, port = server_info.split(':') if ":" in server_info else (server_info, "")
    return {
        "name": urllib.parse.unquote(name),
        "type": "ss",
        "server": server,
        "port": int(port),
        "cipher": cipher,
        "password": password,
        "udp": True
    }

def parse_trojan_link(link):
    link = link[9:]
    config_part, name = link.split('#')
    user_info, host_info = config_part.split('@')
    username, password = user_info.split(':') if ":" in user_info else ("", user_info)
    host, port_and_query = host_info.split(':') if ":" in host_info else (host_info, "")
    port, query = port_and_query.split('?', 1) if '?' in port_and_query else (port_and_query, "")
    return {
        "name": urllib.parse.unquote(name),
        "type": "trojan",
        "server": host,
        "port": int(port),
        "password": password,
        "sni": urllib.parse.parse_qs(query).get("sni", [""])[0],
        "skip-cert-verify": urllib.parse.parse_qs(query).get("skip-cert-verify", ["false"])[0] == "true"
    }

def parse_vless_link(link):
    link = link[8:]
    config_part, name = link.split('#')
    user_info, host_info = config_part.split('@')
    uuid = user_info
    host, query = host_info.split('?', 1) if '?' in host_info else (host_info, "")
    port = host.split(':')[-1] if ':' in host else ""
    host = host.split(':')[0] if ':' in host else ""
    return {
        "name": urllib.parse.unquote(name),
        "type": "vless",
        "server": host,
        "port": int(port),
        "uuid": uuid,
        "security": urllib.parse.parse_qs(query).get("security", ["none"])[0],
        "tls": urllib.parse.parse_qs(query).get("security", ["none"])[0] == "tls",
        "sni": urllib.parse.parse_qs(query).get("sni", ""),
        "skip-cert-verify": urllib.parse.parse_qs(query).get("skip-cert-verify", ["false"])[0] == "true",
        "network": urllib.parse.parse_qs(query).get("type", ["tcp"])[0],
        "ws-opts": {
            "path": urllib.parse.parse_qs(query).get("path", [""])[0],
            "headers": {
                "Host": urllib.parse.parse_qs(query).get("host", [""])[0]
            }
        } if urllib.parse.parse_qs(query).get("type", ["tcp"])[0] == "ws" else {}
    }

def parse_vmess_link(link):
    link = link[8:]
    decoded_link = base64.urlsafe_b64decode(link + '=' * (-len(link) % 4)).decode("utf-8")
    vmess_info = json.loads(decoded_link)
    return {
        "name": urllib.parse.unquote(vmess_info.get("ps", "vmess")),
        "type": "vmess",
        "server": vmess_info["add"],
        "port": int(vmess_info["port"]),
        "uuid": vmess_info["id"],
        "alterId": int(vmess_info.get("aid", 0)),
        "cipher": "auto",
        "network": vmess_info.get("net", "tcp"),
        "tls": vmess_info.get("tls", "") == "tls",
        "sni": vmess_info.get("sni", ""),
        "ws-opts": {
            "path": vmess_info.get("path", ""),
            "headers": {
                "Host": vmess_info.get("host", "")
            }
        } if vmess_info.get("net", "tcp") == "ws" else {}
    }

def parse_ss_sub(link):
    new_links = []
    try:
        response = requests.get(link, headers=headers, verify=False, allow_redirects=True)
        if response.status_code == 200:
            data = response.json()
            new_links = [{"name": x['remarks'], "type": "ss", "server": x['server'], "port": x['server_port'],
                          "cipher": x['method'], "password": x['password'], "udp": True} for x in data]
            return new_links
    except requests.RequestException as e:
        logging.error(f"请求错误: {e}")
        return new_links

def parse_md_link(link):
    try:
        response = requests.get(link)
        response.raise_for_status()
        content = response.text
        content = urllib.parse.unquote(content)
        pattern = r'(?:vless|vmess|trojan|hysteria2|ss):\/\/[^#\s]*(?:#[^\s]*)?'
        matches = re.findall(pattern, content)
        return matches
    except requests.RequestException as e:
        logging.error(f"请求错误: {e}")
        return []

async def js_render(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        page = await browser.new_page()
        try:
            await page.goto(url, timeout=4000)
            content = await page.content()
            await browser.close()
            return content
        except Exception as e:
            logging.error(f"Playwright 渲染失败: {e}")
            await browser.close()
            return ""

def match_nodes(text):
    proxy_pattern = r"\{[^}]*name\s*:\s*['\"][^'\"]+['\"][^}]*server\s*:\s*[^,]+[^}]*\}"
    nodes = re.findall(proxy_pattern, text, re.DOTALL)
    proxies_list = []
    for node in nodes:
        try:
            node_dict = yaml.safe_load(node)
            proxies_list.append(node_dict)
        except yaml.YAMLError as e:
            logging.warning(f"无法解析代理节点: {e} - 内容: {node[:50]}...")
    yaml_data = {"proxies": proxies_list}
    return yaml_data

async def process_url(url):
    isyaml = False
    new_links = []
    try:
        async with httpx.AsyncClient(verify=False, follow_redirects=True, headers=headers, timeout=TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            content = response.text
            logging.info(f"成功从 {url} 获取内容")
            
            if 'proxies:' in content:
                if '</pre>' in content:
                    content = content.replace('<pre style="word-wrap: break-word; white-space: pre-wrap;">', '').replace('</pre>', '')
                yaml_data = yaml.safe_load(content)
                if 'proxies' in yaml_data:
                    isyaml = True
                    return yaml_data['proxies'] if yaml_data['proxies'] else [], isyaml
            else:
                try:
                    decoded_bytes = base64.b64decode(content)
                    decoded_content = decoded_bytes.decode('utf-8')
                    decoded_content = urllib.parse.unquote(decoded_content)
                    return decoded_content.splitlines(), isyaml
                except Exception as e:
                    logging.warning(f"内容无法解码，尝试 Playwright 渲染: {e}")
                    content = await js_render(url)
                    if 'external-controller' in content:
                        try:
                            yaml_data = yaml.safe_load(content)
                        except:
                            yaml_data = match_nodes(content)
                        if 'proxies' in yaml_data:
                            isyaml = True
                            return yaml_data['proxies'], isyaml
                    else:
                        pattern = r'([A-Za-z0-9_+/\-]+={0,2})'
                        matches = re.findall(pattern, content)
                        stdout = matches[-1] if matches else []
                        decoded_bytes = base64.b64decode(stdout)
                        decoded_content = decoded_bytes.decode('utf-8')
                        return decoded_content.splitlines(), isyaml
    except httpx.RequestError as e:
        logging.error(f"请求 {url} 时发生错误: {e}")
    except Exception as e:
        logging.error(f"处理 {url} 时发生意外错误: {e}")
    return [], isyaml

def parse_proxy_link(link):
    try:
        if link.startswith(("hysteria2://", "hy2://")):
            return parse_hysteria2_link(link)
        elif link.startswith("trojan://"):
            return parse_trojan_link(link)
        elif link.startswith("ss://"):
            return parse_ss_link(link)
        elif link.startswith("vless://"):
            return parse_vless_link(link)
        elif link.startswith("vmess://"):
            return parse_vmess_link(link)
    except Exception as e:
        logging.warning(f"解析代理链接 {link[:50]}... 失败: {e}")
        return None

def deduplicate_proxies(proxies_list):
    unique_proxies = []
    seen = set()
    for proxy in proxies_list:
        key_tuple = (proxy.get('server'), proxy.get('port'), proxy.get('type'))
        if proxy.get('password'):
            key_tuple += (proxy['password'],)
        if key_tuple not in seen:
            seen.add(key_tuple)
            unique_proxies.append(proxy)
    return unique_proxies

def add_random_suffix(name, existing_names):
    suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=4))
    new_name = f"{name}-{suffix}"
    while new_name in existing_names:
        suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=4))
        new_name = f"{name}-{suffix}"
    return new_name

def read_txt_files(folder_path):
    all_lines = []
    if not os.path.isdir(folder_path):
        return all_lines
    txt_files = glob.glob(os.path.join(folder_path, '*.txt'))
    for file_path in txt_files:
        with open(file_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()
            all_lines.extend(line.strip() for line in lines)
    if all_lines:
        logging.info(f'加载【{folder_path}】目录下所有txt中节点')
    return all_lines

def read_yaml_files(folder_path):
    load_nodes = []
    if not os.path.isdir(folder_path):
        return load_nodes
    yaml_files = glob.glob(os.path.join(folder_path, '*.yaml'))
    yaml_files.extend(glob.glob(os.path.join(folder_path, '*.yml')))
    for file_path in yaml_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                config = yaml.safe_load(file)
                if config and 'proxies' in config:
                    load_nodes.extend(config['proxies'])
        except Exception as e:
            logging.error(f"读取 {file_path} 时出错: {str(e)}")
    if load_nodes:
        logging.info(f'加载【{folder_path}】目录下yaml/yml中所有节点')
    return load_nodes

def filter_by_types_alt(allowed_types, nodes):
    return [x for x in nodes if x.get('type') in allowed_types]

def merge_lists(*lists):
    return [item for item in chain.from_iterable(lists) if item != '']

async def handle_links(new_links, resolve_name_conflicts, cache):
    tasks = []
    for new_link in new_links:
        if new_link.startswith(("hysteria2://", "hy2://", "trojan://", "ss://", "vless://", "vmess://")):
            node = parse_proxy_link(new_link)
            if node:
                resolve_name_conflicts(node, cache)
        else:
            logging.warning(f"跳过无效或不支持的链接: {new_link}")
    await asyncio.gather(*tasks)

async def generate_clash_config(links, load_nodes):
    now = datetime.now()
    logging.info(f"当前时间: {now}\n---")
    final_nodes = []
    existing_names = set()
    config = clash_config_template.copy()
    cache = ExclusionCache()

    def resolve_name_conflicts(node, cache):
        server = node.get("server")
        name = str(node.get("name", "unnamed-node"))
        if not_contains(name, server, cache):
            if name in existing_names:
                name = add_random_suffix(name, existing_names)
            existing_names.add(name)
            node["name"] = name
            final_nodes.append(node)

    for node in load_nodes:
        resolve_name_conflicts(node, cache)

    tasks = []
    for link in links:
        link = link.strip()
        if not link:
            continue
        if link.startswith(("hysteria2://", "hy2://", "trojan://", "ss://", "vless://", "vmess://")):
            node = parse_proxy_link(link)
            if node:
                resolve_name_conflicts(node, cache)
        elif '|links' in link or '.md' in link:
            link = link.replace('|links', '')
            new_links = parse_md_link(link)
            handle_links(new_links, resolve_name_conflicts, cache)
        elif '|ss' in link:
            link = link.replace('|ss', '')
            new_links = parse_ss_sub(link)
            for node in new_links:
                resolve_name_conflicts(node, cache)
        else:
            logging.info(f'当前正在处理link: {link}')
            if '{' in link:
                try:
                    link = resolve_template_url(link)
                except Exception as e:
                    logging.error(f"解析模板URL失败: {e}")
                    continue
            tasks.append(process_url(link))

    all_fetched_links = await asyncio.gather(*tasks, return_exceptions=True)
    
    for result in all_fetched_links:
        if isinstance(result, Exception):
            logging.error(f"获取订阅链接时出错: {result}")
            continue
        new_links, isyaml = result
        if isyaml:
            for node in new_links:
                resolve_name_conflicts(node, cache)
        else:
            await handle_links(new_links, resolve_name_conflicts, cache)

    final_nodes = deduplicate_proxies(final_nodes)
    config["proxy-groups"][1]["proxies"] = []
    for node in final_nodes:
        name = str(node["name"])
        if not_contains(name, node["server"], cache):
            config["proxy-groups"][1]["proxies"].append(name)
            proxies = list(set(config["proxy-groups"][1]["proxies"]))
            config["proxy-groups"][1]["proxies"] = proxies
            config["proxy-groups"][2]["proxies"] = proxies
            config["proxy-groups"][3]["proxies"] = proxies
    config["proxies"] = final_nodes

    if config["proxies"]:
        global CONFIG_FILE
        CONFIG_FILE = CONFIG_FILE[:-5] if CONFIG_FILE.endswith('.json') else CONFIG_FILE
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
            with open(f'{CONFIG_FILE}.json', "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False)
            logging.info(f"已经生成Clash配置文件{CONFIG_FILE}|{CONFIG_FILE}.json")
        except Exception as e:
            logging.error(f"保存配置文件失败: {e}")
    else:
        logging.warning('没有节点数据更新')
    cache.save()

def not_contains(name, server=None, cache=None):
    if not cache:
        cache = ExclusionCache()
    try:
        if any(k in name for k in BAN):
            if not cache.is_excluded(name):
                cache.add_excluded(name, '名称或GeoIP过滤')
            return False
        if server and os.path.exists(GEOIP_DB_PATH):
            try:
                ip_address = socket.gethostbyname(server)
            except (socket.gaierror, ValueError) as e:
                logging.debug(f"无法解析域名 {server}: {e}")
                return True
            try:
                with GeoIPReader(GEOIP_DB_PATH) as reader:
                    response = reader.country(ip_address)
                    if response.country.iso_code == "CN":
                        if not cache.is_excluded(name):
                            cache.add_excluded(name, 'GeoIP过滤')
                        return False
            except Exception as e:
                logging.error(f"GeoIP 过滤错误: {e}")
        return True
    except Exception as e:
        logging.error(f"GeoIP 过滤或名称检查时出错: {e}")
        return not any(k in name for k in BAN)

class ExclusionCache:
    def __init__(self, filename="exclusion_cache.json"):
        self.filename = filename
        self.cache = self.load()

    def load(self):
        try:
            with open(self.filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save(self):
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"保存排除缓存失败: {e}")

    def add_excluded(self, name, reason="未知原因"):
        self.cache[name] = {"reason": reason, "timestamp": datetime.now().isoformat()}

    def is_excluded(self, name):
        if name in self.cache:
            timestamp_str = self.cache[name].get("timestamp")
            if timestamp_str:
                cached_time = datetime.fromisoformat(timestamp_str)
                # 缓存有效期为7天，可自行调整
                if (datetime.now() - cached_time).total_seconds() < 7 * 24 * 3600:
                    return True
        return False

class ClashAPIException(Exception):
    pass

class ProxyTestResult:
    def __init__(self, name: str, delays: List[Optional[float]] = None):
        self.name = name
        self.delays = delays if delays is not None else []
        valid_delays = [d for d in self.delays if d is not None]
        self.success_rate = len(valid_delays) / len(self.delays) if self.delays else 0
        self.average_delay = sum(valid_delays) / len(valid_delays) if valid_delays else float('inf')
        self.std_dev = statistics.stdev(valid_delays) if len(valid_delays) > 1 else 0
        self.status = "ok" if self.is_valid else "fail"
        self.tested_time = datetime.now()

    @property
    def is_valid(self) -> bool:
        return self.success_rate >= MIN_SUCCESS_RATE and self.std_dev <= MAX_STD_DEV and self.average_delay != float('inf')

def ensure_executable(file_path):
    if platform.system().lower() in ['linux', 'darwin']:
        os.chmod(file_path, 0o755)

def handle_clash_error(error_message, config_file_path):
    start_time = time.time()
    config_file_path = f'{config_file_path}.json' if os.path.exists(f'{config_file_path}.json') else config_file_path
    proxy_index_match = re.search(r'proxy (\d+):', error_message)
    if not proxy_index_match:
        return False
    problem_index = int(proxy_index_match.group(1))
    try:
        with open(config_file_path, 'r', encoding='utf-8') as file:
            config = json.load(file)
        problem_proxy_name = config['proxies'][problem_index]['name']
        del config['proxies'][problem_index]
        proxies = config['proxy-groups'][1]["proxies"]
        proxies.remove(problem_proxy_name)
        for group in config["proxy-groups"][1:]:
            group["proxies"] = proxies
        with open(config_file_path, 'w', encoding='utf-8') as file:
            file.write(json.dumps(config, ensure_ascii=False))
        logging.info(f'修复配置异常，移除proxy[{problem_index}] {problem_proxy_name} 完毕，耗时{time.time() - start_time:.2f}s\n')
        return True
    except Exception as e:
        logging.error(f"处理配置文件时出错: {str(e)}")
        return False

def download_and_extract_latest_release():
    url = "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest"
    try:
        response = requests.get(url)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Failed to retrieve latest release info: {e}")
        return
    data = response.json()
    assets = data.get("assets", [])
    os_type = platform.system().lower()
    targets = {
        "darwin": "mihomo-darwin-amd64-compatible",
        "linux": "mihomo-linux-amd64-compatible",
        "windows": "mihomo-windows-amd64-compatible"
    }
    download_url = None
    new_name = f"clash-{os_type}" if os_type != "windows" else "clash.exe"
    if os.path.exists(new_name):
        return
    for asset in assets:
        name = asset.get("name", "")
        if os_type == "darwin" and targets["darwin"] in name and name.endswith('.gz'):
            download_url = asset["browser_download_url"]
            break
        elif os_type == "linux" and targets["linux"] in name and name.endswith('.gz'):
            download_url = asset["browser_download_url"]
            break
        elif os_type == "windows" and targets["windows"] in name and name.endswith('.zip'):
            download_url = asset["browser_download_url"]
            break
    if download_url:
        logging.info(f"正在下载最新 Clash 核心: {download_url}")
        try:
            response = requests.get(download_url, stream=True)
            response.raise_for_status()
            if os_type == "windows":
                with open("clash.zip", "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                with zipfile.ZipFile("clash.zip", "r") as zip_ref:
                    zip_ref.extractall()
                os.remove("clash.zip")
                for file in os.listdir():
                    if targets["windows"] in file:
                        os.rename(file, new_name)
                        break
            else:
                with open("clash.gz", "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                with gzip.open("clash.gz", "rb") as f_in:
                    with open(new_name, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                os.remove("clash.gz")
            ensure_executable(new_name)
            logging.info(f"已下载并解压 Clash 核心到: {new_name}")
        except requests.RequestException as e:
            logging.error(f"下载失败: {e}")
        except Exception as e:
            logging.error(f"文件处理失败: {e}")

def start_clash():
    download_and_extract_latest_release()
    os_type = platform.system().lower()
    clash_binary = f"clash-{os_type}" if os_type != "windows" else "clash.exe"
    if not os.path.exists(clash_binary):
        logging.error(f"未找到 Clash 可执行文件: {clash_binary}")
        sys.exit(1)
    global CONFIG_FILE
    config_file = CONFIG_FILE
    if not os.path.exists(config_file):
        config_file = f'{CONFIG_FILE}.json'
    if not os.path.exists(config_file):
        logging.error(f"未找到配置文件: {config_file}")
        sys.exit(1)
    for proc in psutil.process_iter(['name']):
        if proc.info['name'].startswith('clash'):
            try:
                proc.kill()
                time.sleep(1)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    cmd = [f"./{clash_binary}", "-f", config_file]
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8'
    )
    time.sleep(2)
    if process.poll() is not None:
        stdout, stderr = process.communicate()
        if "Fatal error" in stderr:
            if handle_clash_error(stderr, config_file):
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8'
                )
                time.sleep(2)
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    logging.error(f"Clash 启动失败: {stderr}")
                    sys.exit(1)
            else:
                logging.error(f"Clash 启动失败: {stderr}")
                sys.exit(1)
    for port in CLASH_API_PORTS:
        try:
            response = requests.get(f"http://{CLASH_API_HOST}:{port}/version", timeout=2)
            if response.status_code == 200:
                logging.info(f"Clash API 在端口 {port} 上运行正常")
                break
        except requests.RequestException:
            logging.warning(f"Clash API 端口 {port} 不可达")
            if port == CLASH_API_PORTS[-1]:
                logging.error("所有 Clash API 端口均不可达，启动失败")
                sys.exit(1)
    return process

def switch_proxy(proxy_name):
    with switch_lock:
        max_retries = 3
        for attempt in range(max_retries):
            for port in CLASH_API_PORTS:
                try:
                    url = f"http://{CLASH_API_HOST}:{port}/proxies/节点选择"
                    headers = {"Authorization": f"Bearer {CLASH_API_SECRET}"} if CLASH_API_SECRET else {}
                    data = {"name": urllib.parse.quote(proxy_name, safe='')}
                    response = requests.put(url, headers=headers, json=data, timeout=TIMEOUT)
                    response.raise_for_status()
                    logging.info(f"成功切换到代理节点: {proxy_name}")
                    return True
                except requests.RequestException as e:
                    logging.warning(f"切换代理节点 {proxy_name} 失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    if isinstance(e, requests.HTTPError) and e.response:
                        logging.warning(f"响应内容: {e.response.text}")
                    time.sleep(1)
            if attempt < max_retries - 1:
                logging.info(f"重试切换代理节点 {proxy_name}")
        return False

class ClashAPI:
    def __init__(self, host: str, ports: List[int], secret: str = ""):
        self.host = host
        self.ports = ports
        self.secret = secret
        self.base_url = None
        self.client = httpx.AsyncClient(verify=False)
        self.semaphore = Semaphore(MAX_CONCURRENT_TESTS)
        self.headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        self._test_results_cache = {}

    async def __aenter__(self):
        for port in self.ports:
            base_url = f"http://{self.host}:{port}"
            if await self.check_connection(base_url):
                self.base_url = base_url
                break
        if not self.base_url:
            raise ClashAPIException("无法连接到任何 Clash API 端口")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def check_connection(self, base_url: str = None) -> bool:
        base_url = base_url or self.base_url
        if not base_url:
            return False
        try:
            response = await self.client.get(f"{base_url}/version", headers=self.headers, timeout=TIMEOUT)
            response.raise_for_status()
            logging.info(f"成功连接到 Clash API: {base_url}")
            return True
        except httpx.HTTPStatusError as e:
            logging.error(f"HTTP 错误: {e}")
            return False
        except httpx.RequestError as e:
            logging.error(f"请求错误: {e}")
            return False

    async def test_proxy_delay(self, proxy_name: str, secondary_test: bool = False, cache=None) -> ProxyTestResult:
        if not self.base_url:
            raise ClashAPIException("未建立与 Clash API 的连接")
        if cache and cache.is_excluded(proxy_name):
            logging.info(f"节点 '{proxy_name}' 在排除缓存中，跳过测试。")
            return ProxyTestResult(proxy_name, delays=[None])
        cache_key = f"{proxy_name}_{'secondary' if secondary_test else 'primary'}"
        if cache_key in self._test_results_cache:
            cached_result = self._test_results_cache[cache_key]
            if (datetime.now() - cached_result.tested_time).total_seconds() < 60:
                return cached_result
        async with self.semaphore:
            delays = []
            for _ in range(STABILITY_TESTS):
                try:
                    test_url = SECONDARY_TEST_URL if secondary_test else TEST_URL
                    response = await self.client.get(
                        f"{self.base_url}/proxies/{urllib.parse.quote(proxy_name, safe='')}/delay",
                        headers=self.headers,
                        params={"url": test_url, "timeout": int(TIMEOUT * 1000)}
                    )
                    response.raise_for_status()
                    delay = response.json().get("delay")
                    delays.append(delay)
                except httpx.HTTPError:
                    delays.append(None)
                    if cache:
                        cache.add_excluded(proxy_name, f"{'Secondary' if secondary_test else 'Primary'} 测试失败")
                except Exception as e:
                    delays.append(None)
                    if cache:
                        cache.add_excluded(proxy_name, f"{'Secondary' if secondary_test else 'Primary'} 测试失败")
                if _ < STABILITY_TESTS - 1:
                    await asyncio.sleep(STABILITY_INTERVAL)
            result = ProxyTestResult(proxy_name, delays)
            self._test_results_cache[cache_key] = result
            return result
    
    async def test_proxy_speed(self, proxy_name, cache):
        cache_speed = load_speed_cache()
        cache_key = proxy_name
        if cache_key in cache_speed:
            cached = cache_speed[cache_key]
            if (datetime.now() - datetime.fromisoformat(cached['timestamp'])).total_seconds() < 24 * 3600:
                speed = cached['speed']
                logging.info(f"节点 '{proxy_name}' 速度测试（缓存）: {speed:.2f}Mb/s")
                results_speed.append((proxy_name, f"{speed:.2f}"))
                return speed

        async with self.semaphore:
            if not await self.async_switch_proxy(proxy_name):
                cache.add_excluded(proxy_name, "切换失败")
                logging.warning(f"节点 {proxy_name} 切换失败，跳过速度测试")
                return 0

            start_time = time.time()
            total_length = 0
            test_duration = 10
            max_retries = 3
            retry_count = 0

            async with httpx.AsyncClient(proxies={"http://": 'http://127.0.0.1:7890', "https://": 'http://127.0.0.1:7890'}, verify=False) as client:
                while retry_count < max_retries:
                    try:
                        async with client.stream('GET', SPEED_TEST_URL, headers={'Cache-Control': 'no-cache'}, timeout=test_duration) as response:
                            response.raise_for_status()
                            async for chunk in response.aiter_bytes():
                                total_length += len(chunk)
                                if time.time() - start_time >= test_duration:
                                    break
                        break
                    except (httpx.RequestError, httpx.TimeoutException) as e:
                        retry_count += 1
                        logging.warning(f"测试节点 {proxy_name} 下载失败 (重试 {retry_count}/{max_retries}): {e}")
                        if retry_count == max_retries:
                            cache.add_excluded(proxy_name, f"测速失败")
                            logging.error(f"节点 {proxy_name} 测试失败，跳过")
                            return 0
                        await asyncio.sleep(1)

            elapsed_time = time.time() - start_time
            speed = total_length / elapsed_time / 1024 / 1024 if elapsed_time > 0 else 0
            results_speed.append((proxy_name, f"{speed:.2f}"))
            cache_speed[cache_key] = {"speed": speed, "timestamp": datetime.now().isoformat()}
            save_speed_cache(cache_speed)
            logging.info(f"节点 '{proxy_name}' 速度测试: {speed:.2f}Mb/s")
            return speed

    async def async_switch_proxy(self, proxy_name):
        max_retries = 3
        for attempt in range(max_retries):
            for port in self.ports:
                try:
                    url = f"http://{self.host}:{port}/proxies/节点选择"
                    data = {"name": urllib.parse.quote(proxy_name, safe='')}
                    async with httpx.AsyncClient(verify=False) as client:
                        response = await client.put(url, headers=self.headers, json=data, timeout=TIMEOUT)
                        response.raise_for_status()
                    logging.info(f"成功切换到代理节点: {proxy_name}")
                    return True
                except httpx.RequestError as e:
                    logging.warning(f"切换代理节点 {proxy_name} 失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    if isinstance(e, httpx.HTTPError) and e.response:
                        logging.warning(f"响应内容: {e.response.text}")
                    await asyncio.sleep(1)
        return False

class ClashConfig:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self._load_config()
        self.proxy_groups = self._get_proxy_groups()

    def _load_config(self) -> dict:
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logging.error(f"找不到配置文件: {self.config_path}")
            sys.exit(1)
        except yaml.YAMLError as e:
            logging.error(f"配置文件格式错误: {e}")
            sys.exit(1)

    def _get_proxy_groups(self) -> List[Dict]:
        return self.config.get("proxy-groups", [])

    def get_group_names(self) -> List[str]:
        return [group["name"] for group in self.proxy_groups]

    def get_group_proxies(self, group_name: str) -> List[str]:
        for group in self.proxy_groups:
            if group["name"] == group_name:
                return group.get("proxies", [])
        return []

    def remove_invalid_proxies(self, results: List[ProxyTestResult]):
        invalid_proxies = {r.name for r in results if not r.is_valid}
        if not invalid_proxies:
            return
        valid_proxies_in_config = []
        if "proxies" in self.config:
            valid_proxies_in_config = [p for p in self.config["proxies"]
                             if p.get("name") not in invalid_proxies]
            self.config["proxies"] = valid_proxies_in_config
        for group in self.proxy_groups:
            if "proxies" in group:
                group["proxies"] = [p for p in group["proxies"] if p not in invalid_proxies]
        global LIMIT
        left = LIMIT if len(self.config['proxies']) > LIMIT else len(self.config['proxies'])
        logging.info(f"已从配置中移除 {len(invalid_proxies)} 个失效节点，最终保留{left}个延迟最小的节点")

    def keep_proxies_by_limit(self, proxy_names):
        if "proxies" in self.config:
            self.config["proxies"] = [p for p in self.config["proxies"] if p["name"] in proxy_names]

    def update_group_proxies(self, group_name: str, results: List[ProxyTestResult]):
        self.remove_invalid_proxies(results)
        valid_results = [r for r in results if r.is_valid]
        valid_results = list(set(valid_results))
        valid_results.sort(key=lambda x: x.average_delay)
        proxy_names = [r.name for r in valid_results]
        for group in self.proxy_groups:
            if group["name"] == group_name:
                group["proxies"] = proxy_names
                break
        return proxy_names

    def update_proxies_names(self, name_mapping: Dict[str, str]):
        if "proxies" in self.config:
            for proxy in self.config["proxies"]:
                if proxy["name"] in name_mapping:
                    proxy["name"] = name_mapping[proxy["name"]]
        for group in self.proxy_groups:
            if "proxies" in group:
                group["proxies"] = [name_mapping.get(p, p) for p in group["proxies"]]

    def save(self):
        try:
            yaml_cfg = self.config_path.strip('.json') if self.config_path.endswith('.json') else self.config_path
            with open(yaml_cfg, 'w', encoding='utf-8') as f:
                yaml.dump(self.config, f, allow_unicode=True, sort_keys=False)
            with open(f'{yaml_cfg}.json', "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False)
            logging.info("配置文件保存成功")
        except Exception as e:
            logging.error(f"保存配置文件失败: {e}")
            sys.exit(1)

def print_test_summary(group_name: str, results: List[ProxyTestResult], test_type: str = "Primary"):
    valid_results = [r for r in results if r.is_valid]
    invalid_results = [r for r in results if not r.is_valid]
    total = len(results)
    valid = len(valid_results)
    invalid = len(invalid_results)
    logging.info(f"\n策略组 '{group_name}' {test_type} 测试结果:")
    logging.info(f"总节点数: {total}")
    logging.info(f"可用节点数: {valid}")
    logging.info(f"失效节点数: {invalid}")
    delays = []
    if valid > 0:
        avg_delay = sum(r.average_delay for r in valid_results) / valid
        avg_std_dev = sum(r.std_dev for r in valid_results) / valid
        avg_success_rate = sum(r.success_rate * 100 for r in valid_results) / valid
        logging.info(f"平均延迟: {avg_delay:.2f}ms")
        logging.info(f"平均标准差: {avg_std_dev:.2f}ms (波动性)")
        logging.info(f"平均成功率: {avg_success_rate:.2f}%")
        logging.info(f"\n{test_type} 节点延迟统计 (按平均延迟排序):")
        sorted_results = sorted(valid_results, key=lambda x: x.average_delay)
        for i, result in enumerate(sorted_results[:LIMIT], 1):
            delays.append({"name": result.name, "Avg_Delay_ms": round(result.average_delay, 2), "Std_Dev": round(result.std_dev, 2), "Success_Rate": round(result.success_rate * 100, 2)})
            logging.info(f"{i}. {result.name}: 平均 {result.average_delay:.2f}ms, 标准差 {result.std_dev:.2f}ms, 成功率 {result.success_rate * 100:.2f}%")
    return delays

async def test_group_proxies(clash_api: ClashAPI, proxies: List[str], secondary_test: bool = False, cache=None) -> List[ProxyTestResult]:
    test_type = "Secondary" if secondary_test else "Primary"
    logging.info(f"开始{test_type}测试 {len(proxies)} 个节点 (最大并发: {MAX_CONCURRENT_TESTS})")
    tasks = [clash_api.test_proxy_delay(proxy_name, secondary_test=secondary_test, cache=cache) for proxy_name in proxies]
    results = []
    for future in asyncio.as_completed(tasks):
        result = await future
        results.append(result)
        done = len(results)
        total = len(tasks)
        print(f"\r{test_type} 测试进度: {done}/{total} ({done / total * 100:.1f}%)", end="", flush=True)
    print()
    return results

class ClashManager:
    def __init__(self, config_file: str):
        self.config_file = config_file
        self.config = ClashConfig(config_file)
        self.cache = ExclusionCache()
        self.api = ClashAPI(CLASH_API_HOST, CLASH_API_PORTS, CLASH_API_SECRET)
        self.executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_TESTS)

    async def run_tests(self):
        logging.info("===================节点批量检测基本信息======================")
        logging.info(f"配置文件: {self.config_file}")
        logging.info(f"API 端口: {CLASH_API_PORTS[0]}")
        logging.info(f"并发数量: {MAX_CONCURRENT_TESTS}")
        logging.info(f"超时时间: {TIMEOUT}秒")
        logging.info(f"保留节点：最多保留{LIMIT}个延迟最小的有效节点")
        logging.info(f'加载配置文件{self.config_file}')
        
        available_groups = self.config.get_group_names()[1:]
        groups_to_test = available_groups
        if not groups_to_test:
            logging.error("错误: 没有找到要测试的有效策略组")
            logging.info(f"可用的策略组: {', '.join(available_groups)}")
            return
        
        logging.info(f"\n将测试以下策略组: {', '.join(groups_to_test)}")
        start_time = datetime.now()
        
        try:
            async with self.api as clash_api:
                if not await clash_api.check_connection():
                    return
                
                all_test_results = []
                group_name = groups_to_test[0]
                logging.info(f"\n======================== 开始 Primary 测试策略组: {group_name} ====================")
                proxies = self.config.get_group_proxies(group_name)
                if not proxies:
                    logging.warning(f"策略组 '{group_name}' 中没有代理节点")
                    return
                
                primary_results = await clash_api.test_group_proxies(proxies, secondary_test=False, cache=self.cache)
                all_test_results.extend(primary_results)
                print_test_summary(group_name, primary_results, test_type="Primary")
                
                valid_proxies = [r.name for r in primary_results if r.is_valid]
                if not valid_proxies:
                    logging.warning("没有节点通过 Primary 测试，停止后续测试")
                    return
                
                logging.info(f"\n======================== 开始 Secondary 测试策略组: {group_name} ====================")
                secondary_results = await clash_api.test_group_proxies(valid_proxies, secondary_test=True, cache=self.cache)
                all_test_results.extend(secondary_results)
                print_test_summary(group_name, secondary_results, test_type="Secondary")
                
                valid_proxies = [r.name for r in secondary_results if r.is_valid]
                if not valid_proxies:
                    logging.warning("没有节点通过 Secondary 测试，停止后续测试")
                    return
                
                logging.info('\n===================移除失效节点并按延迟排序======================\n')
                self.config.remove_invalid_proxies(all_test_results)
                
                group_proxies = self.config.get_group_proxies(group_name)
                group_results = [r for r in secondary_results if r.name in group_proxies and r.is_valid]
                group_results.sort(key=lambda x: x.average_delay)
                
                proxy_names = [r.name for r in group_results[:LIMIT]]
                
                self.config.update_group_proxies(group_name, group_results)
                
                if LIMIT:
                    self.config.keep_proxies_by_limit(proxy_names)
                
                if SPEED_TEST:
                    logging.info('\n===================检测节点速度======================\n')
                    name_mapping = await self.start_download_test(proxy_names, speed_limit=0.1)
                    self.config.update_proxies_names(name_mapping)
                    
                self.config.save()
                total_time = (datetime.now() - start_time).total_seconds()
                logging.info(f"\n总耗时: {total_time:.2f} 秒")
                self.cache.save()
                
        except ClashAPIException as e:
            logging.error(f"Clash API 错误: {e}")
        except Exception as e:
            logging.error(f"发生错误: {e}")
            raise
    
    async def start_download_test(self, proxy_names, speed_limit=0.1):
        test_proxies = [name for name in proxy_names if not self.cache.is_excluded(name)][:SPEED_TEST_LIMIT]
        if not test_proxies:
            logging.warning("所有节点都在排除缓存中，跳过测速。")
            return {}

        tasks = [self.api.test_proxy_speed(name, self.cache) for name in test_proxies]
        await asyncio.gather(*tasks)

        filtered_list = [(name, float(speed)) for name, speed in results_speed if float(speed) >= float(f'{speed_limit}')]
        
        sorted_list = sorted(filtered_list, key=lambda x: x[1], reverse=True)
        logging.info(f'节点速度统计:')
        name_mapping = {}
        sorted_proxy_names = []
        for i, (proxy_name, speed) in enumerate(sorted_list[:LIMIT], 1):
            base_name = re.sub(r'(_\d+\.\d+Mb/s)+$', '', proxy_name)
            new_name = f"{base_name}_{speed:.2f}Mb/s"
            sorted_proxy_names.append(new_name)
            name_mapping[proxy_name] = new_name
            logging.info(f"{i}. {new_name}: {speed:.2f}Mb/s")
        
        added_elements = set(name_mapping.values())
        for item in proxy_names:
            if item not in name_mapping and item not in added_elements:
                name_mapping[item] = item
                added_elements.add(item)
        
        return name_mapping

def parse_datetime_variables():
    now = datetime.now()
    return {
        'Y': str(now.year),
        'm': str(now.month).zfill(2),
        'd': str(now.day).zfill(2),
        'H': str(now.hour).zfill(2),
        'M': str(now.minute).zfill(2),
        'S': str(now.second).zfill(2)
    }

def strip_proxy_prefix(url):
    proxy_pattern = r'^https?://[^/]+/https://'
    match = re.match(proxy_pattern, url)
    if match:
        real_url = re.sub(proxy_pattern, 'https://', url)
        proxy_prefix = url[:match.end() - 8]
        return real_url, proxy_prefix
    return url, None

def is_github_raw_url(url):
    return 'raw.githubusercontent.com' in url

def extract_file_pattern(url):
    match = re.search(r'\{x\}(\.[a-zA-Z0-9]+)(?:/|$)', url)
    if match:
        return match.group(1)
    return None

def get_github_filename(github_url, file_suffix):
    match = re.match(r'https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/[^/]+/[^/]+/([^/]+)', github_url)
    if not match:
        raise ValueError("无法从URL中提取owner和repo信息")
    owner, repo, branch = match.groups()
    path_part = github_url.split(f'/refs/heads/{branch}/')[-1]
    path_part = re.sub(r'\{x\}' + re.escape(file_suffix) + '(?:/|$)', '', path_part)
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path_part}"
    response = requests.get(api_url)
    if response.status_code != 200:
        raise Exception(f"GitHub API请求失败: {response.status_code} {response.text}")
    files = response.json()
    matching_files = [f['name'] for f in files if f['name'].endswith(file_suffix)]
    if not matching_files:
        raise Exception(f"未找到匹配的{file_suffix}文件")
    return matching_files[0]

def resolve_template_url(template_url):
    url, proxy_prefix = strip_proxy_prefix(template_url)
    datetime_vars = parse_datetime_variables()
    resolved_url = parse_template(url, datetime_vars)
    if is_github_raw_url(resolved_url) and '{x}' in resolved_url:
        file_suffix = extract_file_pattern(resolved_url)
        if file_suffix:
            filename = get_github_filename(resolved_url, file_suffix)
            resolved_url = re.sub(r'\{x\}' + re.escape(file_suffix), filename, resolved_url)
    if proxy_prefix:
        resolved_url = f"{proxy_prefix}{resolved_url}"
    return resolved_url

def parse_template(template_url, datetime_vars):
    def replace_template(match):
        template_content = match.group(1)
        if template_content == 'x':
            return '{x}'
        result = ''
        current_char = ''
        for char in template_content:
            if char in datetime_vars:
                if current_char:
                    result += current_char
                    current_char = ''
                result += datetime_vars[char]
            else:
                current_char += char
        if current_char:
            result += current_char
        return result
    return re.sub(r'\{([^}]+)\}', replace_template, template_url)

def load_speed_cache():
    cache_file = "speed_cache.json"
    cache = {}
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache = json.load(f)
        except Exception as e:
            logging.warning(f"加载速度缓存失败: {e}")
            pass
    return cache

def save_speed_cache(cache):
    cache_file = "speed_cache.json"
    try:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"保存速度缓存失败: {e}")

def upload_and_generate_urls(file_path=CONFIG_FILE):
    result = {"clash_url": None, "singbox_url": None}
    try:
        if not os.path.isfile(file_path):
            logging.error(f"错误：文件 {file_path} 不存在。")
            return result
        if os.path.getsize(file_path) > 209715200:
            logging.error("错误：文件大小超过 200MB 限制。")
            return result
        subs_file = "subs.json"
        try:
            subs_data = {"clash": [], "singbox": []}
            if os.path.exists(subs_file):
                try:
                    with open(subs_file, 'r', encoding='utf-8') as f:
                        subs_data = json.load(f)
                except:
                    pass
            with open(subs_file, 'w', encoding='utf-8') as f:
                json.dump(subs_data, f, ensure_ascii=False, indent=2)
            logging.info(f"已将订阅链接记录到 {subs_file}")
        except Exception as e:
            logging.error(f"记录订阅链接失败: {str(e)}")
    except Exception as e:
        logging.error(f"发生错误：{e}")
    return result

def work(links, check=False, allowed_types=[], only_check=False):
    try:
        if not only_check:
            load_nodes = read_yaml_files(folder_path=INPUT)
            if allowed_types:
                load_nodes = filter_by_types_alt(allowed_types, nodes=load_nodes)
            links = merge_lists(read_txt_files(folder_path=INPUT), links)
            if links or load_nodes:
                asyncio.run(generate_clash_config(links, load_nodes))
        if check or only_check:
            clash_process = None
            try:
                logging.info(f"===================启动clash并初始化配置======================")
                clash_process = start_clash()
                switch_proxy('DIRECT')
                
                config_file = f'{CONFIG_FILE}.json' if os.path.exists(f'{CONFIG_FILE}.json') else CONFIG_FILE
                if os.path.exists(config_file):
                    manager = ClashManager(config_file)
                    asyncio.run(manager.run_tests())
                else:
                    logging.error("配置文件不存在，无法执行节点检测。")
                
                logging.info(f'批量检测完毕')
            except Exception as e:
                logging.error("执行 Clash API 任务时出错:", exc_info=True)
            finally:
                logging.info(f'关闭Clash API')
                if clash_process is not None:
                    clash_process.kill()
    except KeyboardInterrupt:
        logging.info("\n用户中断执行")
        sys.exit(0)
    except Exception as e:
        logging.error(f"程序执行失败: {e}", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    links = [
        "https://raw.githubusercontent.com/qjlxg/vt/refs/heads/main/link_cleaned.yaml"
    ]
    work(links, check=True, only_check=False, allowed_types=["ss", "hysteria2", "hy2", "vless", "vmess", "trojan"])
