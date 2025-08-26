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
	resultsFile       = flag.String("results-file", "speed-test-results.json", "file to save and load test results")
)

const (
	colorRed    = "\033[31m"
	colorGreen  = "\033[32m"
	colorYellow = "\033[33m"
	colorReset  = "\033[0m"
)

// saveResultsToFile 将测试结果保存到 JSON 文件
func saveResultsToFile(results []*speedtester.Result, filename string) error {
	data, err := json.MarshalIndent(results, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filename, data, 0644)
}

// loadResultsFromFile 从 JSON 文件加载测试结果
func loadResultsFromFile(filename string) ([]*speedtester.Result, error) {
	data, err := os.ReadFile(filename)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil // 文件不存在是正常情况，返回空结果
		}
		return nil, err
	}
	var results []*speedtester.Result
	if err := json.Unmarshal(data, &results); err != nil {
		return nil, err
	}
	return results, nil
}

func main() {
	flag.Parse()
	log.SetLevel(log.SILENT)

	if *configPathsConfig == "" {
		log.Fatalln("please specify the configuration file")
	}

	speedTester := speedtester.New(&speedtester.Config{
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

	allProxies, err := speedTester.LoadProxies(*stashCompatible)
	if err != nil {
		log.Fatalln("load proxies failed: %v", err)
	}

	// 加载上次的测试结果
	previousResults, err := loadResultsFromFile(*resultsFile)
	if err != nil {
		log.Fatalln("failed to load previous results: %v", err)
	}

	// 将上次所有的结果转换为 map，以便快速查找
	previousResultsMap := make(map[string]*speedtester.Result)
	for _, result := range previousResults {
		previousResultsMap[result.ProxyName] = result
	}

	// 筛选出需要重新测试的代理节点（即新增的节点）
	proxiesToTest := make([]*speedtester.Proxy, 0)
	for _, proxy := range allProxies {
		// 如果这个节点不在上次的结果中，就重新测试
		if _, ok := previousResultsMap[proxy.Name()]; !ok {
			proxiesToTest = append(proxiesToTest, proxy)
		} else {
			log.Infoln("Skipping already tested proxy: %s", proxy.Name())
		}
	}

	if len(proxiesToTest) == 0 {
		fmt.Println("没有发现新节点，无需重新测试。")
		printResults(previousResults)
		if *outputPath != "" {
			err = saveOptimizedConfig(previousResults)
			if err != nil {
				log.Fatalln("save config file failed: %v", err)
			}
			fmt.Printf("\nsave config file to: %s\n", *outputPath)
		}
		return
	}

	fmt.Printf("开始测试 %d 个新增节点...\n", len(proxiesToTest))
	bar := progressbar.Default(int64(len(proxiesToTest)), "测试中...")
	newResults := make([]*speedtester.Result, 0)
	var mu sync.Mutex

	speedTester.TestProxies(proxiesToTest, func(result *speedtester.Result) {
		bar.Add(1)
		bar.Describe(result.ProxyName)
		mu.Lock()
		newResults = append(newResults, result)
		mu.Unlock()
	})

	// 合并新旧结果
	finalResults := make([]*speedtester.Result, 0, len(allProxies))
	// 将上次的所有结果拷贝过来
	finalResults = append(finalResults, previousResults...)
	// 遍历本次新测试的结果
	for _, newResult := range newResults {
		found := false
		// 检查它是否是新节点，如果不是，则更新它
		for i, oldResult := range finalResults {
			if oldResult.ProxyName == newResult.ProxyName {
				finalResults[i] = newResult
				found = true
				break
			}
		}
		// 如果是新节点，则追加到结果中
		if !found {
			finalResults = append(finalResults, newResult)
		}
	}

	// 重新排序
	sort.Slice(finalResults, func(i, j int) bool {
		return finalResults[i].DownloadSpeed > finalResults[j].DownloadSpeed
	})

	printResults(finalResults)

	// 保存完整的测试结果以备下次使用
	if err := saveResultsToFile(finalResults, *resultsFile); err != nil {
		log.Fatalln("failed to save final results: %v", err)
	}
	fmt.Printf("complete results saved to: %s\n", *resultsFile)

	if *outputPath != "" {
		err = saveOptimizedConfig(finalResults)
		if err != nil {
			log.Fatalln("save config file failed: %v", err)
		}
		fmt.Printf("\nsave config file to: %s\n", *outputPath)
	}
}

// saveOptimizedConfig 根据测速结果生成优化的 Clash 配置文件
func saveOptimizedConfig(results []*speedtester.Result) error {
	proxies := make([]map[string]any, 0)
	proxyNames := []string{}

	filteredResults := make([]*speedtester.Result, 0)
	for _, result := range results {
		// 过滤不合格的节点
		if *maxLatency > 0 && result.Latency > *maxLatency {
			continue
		}
		if *downloadSize > 0 && *minDownloadSpeed > 0 && result.DownloadSpeed < *minDownloadSpeed*1024*1024 {
			continue
		}
		if *uploadSize > 0 && *minUploadSpeed > 0 && result.UploadSpeed < *minUploadSpeed*1024*1024 {
			continue
		}
		filteredResults = append(filteredResults, result)
	}

	if *renameNodes {
		const concurrentLimit = 10
		var wg sync.WaitGroup
		sem := make(chan struct{}, concurrentLimit)

		for _, result := range filteredResults {
			wg.Add(1)
			sem <- struct{}{}
			go func(result *speedtester.Result) {
				defer wg.Done()
				defer func() { <-sem }()

				proxyConfig := result.ProxyConfig
				location, err := getIPLocation(proxyConfig["server"].(string))
				if err == nil && location.CountryCode != "" {
					newName := generateNodeName(location.CountryCode, result.DownloadSpeed)
					proxyConfig["name"] = newName
				}
			}(result)
		}
		wg.Wait()
	}

	for _, result := range filteredResults {
		proxyConfig := result.ProxyConfig
		proxyNames = append(proxyNames, proxyConfig["name"].(string))
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

		packetLossStr := result.FormatPacketLoss()
		if result.PacketLoss < 10 {
			packetLossStr = colorGreen + packetLossStr + colorReset
		} else if result.PacketLoss < 20 {
			packetLossStr = colorYellow + packetLossStr + colorReset
		} else {
			packetLossStr = colorRed + packetLossStr + colorReset
		}

		downloadSpeed := result.DownloadSpeed / (1024 * 1024)
		downloadSpeedStr := result.FormatDownloadSpeed()
		if downloadSpeed >= 10 {
			downloadSpeedStr = colorGreen + downloadSpeedStr + colorReset
		} else if downloadSpeed >= 5 {
			downloadSpeedStr = colorYellow + downloadSpeedStr + colorReset
		} else {
			downloadSpeedStr = colorRed + downloadSpeedStr + colorReset
		}

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
	"BR": "🇧🇷", "IN": "🇮🇳", "TH": "🇹🇭", "MY": "🇲🇾", "🇲🇳": "🇲🇳", "VN": "🇻🇳", "PH": "🇵🇭", "ID": "🇮🇩", "UA": "🇺🇦",
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
