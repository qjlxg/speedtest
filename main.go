package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/faceair/clash-speedtest/speedtester"
	"github.com/metacubex/mihomo/log"
	"github.com/olekukonko/tablewriter"
	"github.com/schollz/progressbar/v3"
	"gopkg.in/yaml.v3"
)

var (
	configPathsConfig = flag.String("c", "", "config file path, also support http(s) url")
	filterRegexConfig = flag.String("f", ".+", "filter proxies by name, use regexp")
	blockKeywords     = flag.String("b", "", "block proxies by keywords, use | to separate multiple keywords (example: -b 'rate|x1|1x')")
	serverURL         = flag.String("server-url", "https://speed.cloudflare.com", "server url")
	downloadSize      = flag.Int("download-size", 50*1024*1024, "download size for testing proxies")
	uploadSize        = flag.Int("upload-size", 20*1024*1024, "upload size for testing proxies")
	timeout           = flag.Duration("timeout", time.Second*5, "timeout for testing proxies")
	concurrent        = flag.Int("concurrent", 20, "download concurrent size")
	outputPath        = flag.String("output", "", "output config file path")
	stashCompatible   = flag.Bool("stash-compatible", false, "enable stash compatible mode")
	maxLatency        = flag.Duration("max-latency", 800*time.Millisecond, "filter latency greater than this value")
	minDownloadSpeed  = flag.Float64("min-download-speed", 5, "filter download speed less than this value(unit: MB/s)")
	minUploadSpeed    = flag.Float64("min-upload-speed", 2, "filter upload speed less than this value(unit: MB/s)")
	renameNodes       = flag.Bool("rename", false, "rename nodes with IP location and speed")
	fastMode          = flag.Bool("fast", false, "fast mode, only test latency")
)

const (
	colorRed    = "\033[31m"
	colorGreen  = "\033[32m"
	colorYellow = "\033[33m"
	colorReset  = "\033[0m"
)

func main() {
	flag.Parse()
	log.SetLevel(log.SILENT)

	if *configPathsConfig == "" {
		log.Fatalln("please specify the configuration file")
	}

	tester := speedtester.New(&speedtester.Config{
		ConfigPaths:      *configPathsConfig,
		FilterRegex:      *filterRegexConfig,
		BlockRegex:       *blockKeywords,
		ServerURL:        *serverURL,
		DownloadSize:     *downloadSize,
		UploadSize:       *uploadSize,
		Timeout:          *timeout,
		Concurrent:       *concurrent,
		MaxLatency:       *maxLatency,
		MinDownloadSpeed: *minDownloadSpeed * 1024 * 1024,
		MinUploadSpeed:   *minUploadSpeed * 1024 * 1024,
		FastMode:         *fastMode,
	})

	proxies, err := tester.LoadProxies(*stashCompatible)
	if err != nil {
		log.Fatalln("load proxies failed: %v", err)
	}

	// ----------------------
	// 第一步: 快速 Ping 测试，筛选出可用节点
	// ----------------------
	fmt.Println("正在进行快速延迟测试...")
	pingBar := progressbar.Default(int64(len(proxies)), "Ping测试中...")
	pingResults := make([]*speedtester.Result, 0, len(proxies))
	var mu sync.Mutex

	tester.TestProxies(proxies, func(result *speedtester.Result) {
		pingBar.Add(1)
		pingBar.Describe(result.ProxyName)
		mu.Lock()
		pingResults = append(pingResults, result)
		mu.Unlock()
	})

	var goodProxies []*speedtester.Proxy
	for _, res := range pingResults {
		if res.Latency > 0 && res.Latency < *maxLatency {
			goodProxies = append(goodProxies, res.Proxy)
		}
	}
	fmt.Printf("\nPing测试完成，找到 %d 个可用节点。\n", len(goodProxies))

	if len(goodProxies) == 0 {
		fmt.Println("没有找到任何可用的节点。")
		return
	}

	// ----------------------
	// 第二步: 完整测速，只针对筛选后的节点
	// ----------------------
	fmt.Println("\n开始对可用节点进行完整测速...")
	resultsBar := progressbar.Default(int64(len(goodProxies)), "下载测速中...")
	results := make([]*speedtester.Result, 0, len(goodProxies))
	mu = sync.Mutex{}

	tester.TestProxies(goodProxies, func(result *speedtester.Result) {
		resultsBar.Add(1)
		resultsBar.Describe(result.ProxyName)
		mu.Lock()
		results = append(results, result)
		mu.Unlock()
	})

	// ----------------------
	// 第三步: 排序、打印和保存结果
	// ----------------------
	sort.Slice(results, func(i, j int) bool {
		return results[i].DownloadSpeed > results[j].DownloadSpeed
	})

	printResults(results)

	if *outputPath != "" {
		err = saveOptimizedConfig(results)
		if err != nil {
			log.Fatalln("save config file failed: %v", err)
		}
		fmt.Printf("\nsave config file to: %s\n", *outputPath)
	}
}

// saveOptimizedConfig 负责根据测速结果生成优化的Clash配置文件
func saveOptimizedConfig(results []*speedtester.Result) error {
	proxies := make([]map[string]any, 0)
	proxyNames := []string{}

	// 过滤并处理节点
	for _, result := range results {
		// 现有的过滤逻辑
		if *maxLatency > 0 && result.Latency > *maxLatency {
			continue
		}
		if *downloadSize > 0 && *minDownloadSpeed > 0 && result.DownloadSpeed < *minDownloadSpeed*1024*1024 {
			continue
		}
		if *uploadSize > 0 && *minUploadSpeed > 0 && result.UploadSpeed < *minUploadSpeed*1024*1024 {
			continue
		}

		proxyConfig := result.ProxyConfig
		if *renameNodes {
			location, err := getIPLocation(proxyConfig["server"].(string))
			if err != nil || location.CountryCode == "" {
				proxies = append(proxies, proxyConfig)
				proxyNames = append(proxyNames, proxyConfig["name"].(string))
				continue
			}
			newName := generateNodeName(location.CountryCode, result.DownloadSpeed)
			proxyConfig["name"] = newName
			proxyNames = append(proxyNames, newName)
		} else {
			proxyNames = append(proxyNames, proxyConfig["name"].(string))
		}
		proxies = append(proxies, proxyConfig)
	}

	// 创建一个自动选择的代理组
	proxyGroups := []map[string]interface{}{
		{
			"name":     "自动选择",
			"type":     "url-test",
			"url":      "http://www.gstatic.com/generate_204",
			"interval": 300,
			"proxies":  proxyNames,
		},
	}

	// 创建新的 Clash YAML 配置结构
	newConfig := map[string]interface{}{
		"proxies":      proxies,
		"proxy-groups": proxyGroups,
		"rules": []string{
			"MATCH,自动选择",
		},
	}

	yamlData, err := yaml.Marshal(newConfig)
	if err != nil {
		return err
	}

	return os.WriteFile(*outputPath, yamlData, 0o644)
}

func printResults(results []*speedtester.Result) {
	table := tablewriter.NewWriter(os.Stdout)
	var headers []string
	if *fastMode {
		headers = []string{
			"序号",
			"节点名称",
			"类型",
			"延迟",
		}
	} else {
		headers = []string{
			"序号",
			"节点名称",
			"类型",
			"延迟",
			"抖动",
			"丢包率",
			"下载速度",
			"上传速度",
		}
	}
	table.SetHeader(headers)

	table.SetAutoWrapText(false)
	table.SetAutoFormatHeaders(true)
	table.SetHeaderAlignment(tablewriter.ALIGN_LEFT)
	table.SetAlignment(tablewriter.ALIGN_LEFT)
	table.SetCenterSeparator("")
	table.SetColumnSeparator("")
	table.SetRowSeparator("")
	table.SetHeaderLine(false)
	table.SetBorder(false)
	table.SetTablePadding("\t")
	table.SetNoWhiteSpace(true)
	table.SetColMinWidth(0, 4)
	table.SetColMinWidth(1, 20)
	table.SetColMinWidth(2, 8)
	table.SetColMinWidth(3, 8)
	if !*fastMode {
		table.SetColMinWidth(4, 8)
		table.SetColMinWidth(5, 8)
		table.SetColMinWidth(6, 12)
		table.SetColMinWidth(7, 12)
	}

	for i, result := range results {
		idStr := fmt.Sprintf("%d.", i+1)

		// 延迟颜色
		latencyStr := result.FormatLatency()
		if result.Latency > 0 {
			if result.Latency < 800*time.Millisecond {
				latencyStr = colorGreen + latencyStr + colorReset
			} else if result.Latency < 1500*time.Millisecond {
				latencyStr = colorYellow + latencyStr + colorReset
			} else {
				latencyStr = colorRed + latencyStr + colorReset
			}
		} else {
			latencyStr = colorRed + latencyStr + colorReset
		}

		jitterStr := result.FormatJitter()
		if result.Jitter > 0 {
			if result.Jitter < 800*time.Millisecond {
				jitterStr = colorGreen + jitterStr + colorReset
			} else if result.Jitter < 1500*time.Millisecond {
				jitterStr = colorYellow + jitterStr + colorReset
			} else {
				jitterStr = colorRed + jitterStr + colorReset
			}
		} else {
			jitterStr = colorRed + jitterStr + colorReset
		}

		// 丢包率颜色
		packetLossStr := result.FormatPacketLoss()
		if result.PacketLoss < 10 {
			packetLossStr = colorGreen + packetLossStr + colorReset
		} else if result.PacketLoss < 20 {
			packetLossStr = colorYellow + packetLossStr + colorReset
		} else {
			packetLossStr = colorRed + packetLossStr + colorReset
		}

		// 下载速度颜色 (以MB/s为单位判断)
		downloadSpeed := result.DownloadSpeed / (1024 * 1024)
		downloadSpeedStr := result.FormatDownloadSpeed()
		if downloadSpeed >= 10 {
			downloadSpeedStr = colorGreen + downloadSpeedStr + colorReset
		} else if downloadSpeed >= 5 {
			downloadSpeedStr = colorYellow + downloadSpeedStr + colorReset
		} else {
			downloadSpeedStr = colorRed + downloadSpeedStr + colorReset
		}

		// 上传速度颜色
		uploadSpeed := result.UploadSpeed / (1024 * 1024)
		uploadSpeedStr := result.FormatUploadSpeed()
		if uploadSpeed >= 5 {
			uploadSpeedStr = colorGreen + uploadSpeedStr + colorReset
		} else if uploadSpeed >= 2 {
			uploadSpeedStr = colorYellow + uploadSpeedStr + colorReset
		} else {
			uploadSpeedStr = colorRed + uploadSpeedStr + colorReset
		}

		var row []string
		if *fastMode {
			row = []string{
				idStr,
				result.ProxyName,
				result.ProxyType,
				latencyStr,
			}
		} else {
			row = []string{
				idStr,
				result.ProxyName,
				result.ProxyType,
				latencyStr,
				jitterStr,
				packetLossStr,
				downloadSpeedStr,
				uploadSpeedStr,
			}
		}

		table.Append(row)
	}

	fmt.Println()
	table.Render()
	fmt.Println()
}

type IPLocation struct {
	Country     string `json:"country"`
	CountryCode string `json:"countryCode"`
}

var countryFlags = map[string]string{
	"US": "🇺🇸", "CN": "🇨🇳", "GB": "🇬🇧", "UK": "🇬🇧", "JP": "🇯🇵", "DE": "🇩🇪", "FR": "🇫🇷", "RU": "🇷🇺",
	"SG": "🇸🇬", "HK": "🇭🇰", "TW": "🇹🇼", "KR": "🇰🇷", "CA": "🇨🇦", "AU": "🇦🇺", "NL": "🇳🇱", "IT": "🇮🇹",
	"ES": "🇪🇸", "SE": "🇸🇪", "NO": "🇳🇴", "DK": "🇩🇰", "FI": "🇫🇮", "CH": "🇨🇭", "AT": "🇦🇹", "BE": "🇧🇪",
	"BR": "🇧🇷", "IN": "🇮🇳", "TH": "🇹🇭", "MY": "🇲🇾", "VN": "🇻🇳", "PH": "🇵🇭", "ID": "🇮🇩", "UA": "🇺🇦",
	"TR": "🇹🇷", "IL": "🇮🇱", "AE": "🇦🇪", "SA": "🇸🇦", "EG": "🇪🇬", "ZA": "🇿🇦", "NG": "🇳🇬", "KE": "🇰🇪",
	"RO": "🇷🇴", "PL": "🇵🇱", "CZ": "🇨🇿", "HU": "🇭🇺", "BG": "🇧🇬", "HR": "🇭🇷", "SI": "🇸🇮", "SK": "🇸🇰",
	"LT": "🇱🇹", "LV": "🇱🇻", "EE": "🇪🇪", "PT": "🇵🇹", "GR": "🇬🇷", "IE": "🇮🇪", "LU": "🇱🇺", "MT": "🇲🇹",
	"CY": "🇨🇾", "IS": "🇮🇸", "MX": "🇲🇽", "AR": "🇦🇷", "CL": "🇨🇱", "CO": "🇨🇴", "PE": "🇵🇪", "VE": "🇻🇪",
	"EC": "🇪🇨", "UY": "🇺🇾", "PY": "🇵🇾", "BO": "🇧🇴", "CR": "🇨🇷", "PA": "🇵🇦", "GT": "🇬🇹", "HN": "🇭🇳",
	"SV": "🇸🇻", "NI": "🇳🇮", "BZ": "🇧🇿", "JM": "🇯🇲", "TT": "🇹🇹", "BB": "🇧🇧", "GD": "🇬🇩", "LC": "🇱🇨",
	"VC": "🇻🇨", "AG": "🇦🇬", "DM": "🇩🇲", "KN": "🇰🇳", "BS": "🇧🇸", "CU": "🇨🇺", "DO": "🇩🇴", "HT": "🇭🇹",
	"PR": "🇵🇷", "VI": "🇻🇮", "GU": "🇬🇺", "AS": "🇦🇸", "MP": "🇲🇵", "PW": "🇵🇼", "FM": "🇫🇲", "MH": "🇲🇭",
	"KI": "🇰🇮", "TV": "🇹🇻", "NR": "🇳🇷", "WS": "🇼🇸", "TO": "🇹🇴", "FJ": "🇫🇯", "VU": "🇻🇺", "SB": "🇸🇧",
	"PG": "🇵🇬", "NC": "🇳🇨", "PF": "🇵🇫", "WF": "🇼🇫", "CK": "🇨🇰", "NU": "🇳🇺", "TK": "🇹🇰", "SC": "🇸🇨",
}

func getIPLocation(ip string) (*IPLocation, error) {
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Get(fmt.Sprintf("http://ip-api.com/json/%s?fields=country,countryCode", ip))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("failed to get location for IP %s", ip)
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	var location IPLocation
	if err := json.Unmarshal(body, &location); err != nil {
		return nil, err
	}
	return &location, nil
}

func generateNodeName(countryCode string, downloadSpeed float64) string {
	flag, exists := countryFlags[strings.ToUpper(countryCode)]
	if !exists {
		flag = "🏳️"
	}

	speedMBps := downloadSpeed / (1024 * 1024)
	return fmt.Sprintf("%s %s | ⬇️ %.2f MB/s", flag, strings.ToUpper(countryCode), speedMBps)
}
