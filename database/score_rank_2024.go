package database

import (
	"encoding/json"
	"fmt"
	"gaokao-zhiyuan/models"
	"log"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// 一分一段表JSON数据结构
type ScoreRankEntry struct {
	Score      string `json:"score"`
	Num        int    `json:"num"`
	Accumulate int    `json:"accumulate"`
}

type ScoreRankJSON struct {
	Data []ScoreRankEntry `json:"data"`
}

// 多省份2024年一分一段表数据（从官方JSON文件加载）
var scoreRankTables2024 map[string]*models.ScoreRankTable2024

// 初始化函数，加载所有省份的官方一分一段表数据
func init() {
	scoreRankTables2024 = make(map[string]*models.ScoreRankTable2024)
	loadAllProvinceScoreRankData()
}

// loadAllProvinceScoreRankData 从 data/ 目录加载所有省份的一分一段表数据
func loadAllProvinceScoreRankData() {
	dataDir := "data"

	entries, err := os.ReadDir(dataDir)
	if err != nil {
		log.Printf("警告: 无法读取数据目录 %s: %v，尝试从 hubei_data 加载向后兼容数据", dataDir, err)
		// 向后兼容：尝试旧路径
		loadProvinceScoreRankData("hubei", "hubei_data")
		return
	}

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		province := entry.Name()
		provinceDir := filepath.Join(dataDir, province)

		// 检查该省份目录下是否有排名数据文件
		physicsFile := filepath.Join(provinceDir, fmt.Sprintf("ranking_score_%s_physics.json", province))
		historyFile := filepath.Join(provinceDir, fmt.Sprintf("ranking_score_%s_history.json", province))

		if _, err := os.Stat(physicsFile); err == nil {
			loadProvinceScoreRankData(province, provinceDir)
		} else if _, err := os.Stat(historyFile); err == nil {
			loadProvinceScoreRankData(province, provinceDir)
		} else {
			log.Printf("省份 %s 目录下未找到排名数据文件，跳过", province)
		}
	}

	if len(scoreRankTables2024) == 0 {
		log.Printf("警告: 未加载任何省份的一分一段表数据")
	}
}

// loadProvinceScoreRankData 加载指定省份的一分一段表数据
func loadProvinceScoreRankData(province string, dataDir string) {
	table := &models.ScoreRankTable2024{}

	// 加载物理类数据
	physicsFile := filepath.Join(dataDir, fmt.Sprintf("ranking_score_%s_physics.json", province))
	physicsData := loadJSONFile(physicsFile)
	if len(physicsData) > 0 {
		table.Physics = convertToScoreRankData(physicsData)
	}

	// 加载历史类数据
	historyFile := filepath.Join(dataDir, fmt.Sprintf("ranking_score_%s_history.json", province))
	historyData := loadJSONFile(historyFile)
	if len(historyData) > 0 {
		table.History = convertToScoreRankData(historyData)
	}

	if len(table.Physics) > 0 || len(table.History) > 0 {
		scoreRankTables2024[province] = table
		log.Printf("已加载2024年%s省一分一段表数据：物理类 %d 条，历史类 %d 条",
			province, len(table.Physics), len(table.History))
	}
}

// 从JSON文件加载数据
func loadJSONFile(filename string) []ScoreRankEntry {
	// 先尝试原路径
	file, err := os.Open(filename)
	if err != nil {
		// 如果失败，尝试相对于当前工作目录的路径
		log.Printf("尝试打开文件 %s 失败: %v", filename, err)

		// 获取当前工作目录
		pwd, _ := os.Getwd()
		log.Printf("当前工作目录: %s", pwd)

		// 检查文件是否存在
		if _, err := os.Stat(filename); os.IsNotExist(err) {
			log.Printf("文件 %s 不存在，将使用默认数据", filename)
			return []ScoreRankEntry{}
		}

		return []ScoreRankEntry{}
	}
	defer file.Close()

	var jsonData ScoreRankJSON
	decoder := json.NewDecoder(file)
	err = decoder.Decode(&jsonData)
	if err != nil {
		log.Printf("解析JSON文件 %s 失败: %v，将使用默认数据", filename, err)
		return []ScoreRankEntry{}
	}

	log.Printf("成功加载JSON文件 %s，共 %d 条记录", filename, len(jsonData.Data))
	return jsonData.Data
}

// 将JSON数据转换为ScoreRankData格式
func convertToScoreRankData(entries []ScoreRankEntry) []models.ScoreRankData {
	var result []models.ScoreRankData

	for _, entry := range entries {
		// 处理分数字段，可能是单个分数或范围
		scores := parseScoreField(entry.Score)

		for _, score := range scores {
			result = append(result, models.ScoreRankData{
				Score: score,
				Rank:  entry.Accumulate, // 使用累计人数作为排名
			})
		}
	}

	// 按分数降序排列（高分在前）
	for i := 0; i < len(result)-1; i++ {
		for j := i + 1; j < len(result); j++ {
			if result[i].Score < result[j].Score {
				result[i], result[j] = result[j], result[i]
			}
		}
	}

	return result
}

// 解析分数字段，处理单个分数和分数范围
func parseScoreField(scoreStr string) []int {
	var scores []int

	// 处理分数范围，如 "695-750"
	if strings.Contains(scoreStr, "-") {
		parts := strings.Split(scoreStr, "-")
		if len(parts) == 2 {
			start, err1 := strconv.Atoi(parts[0])
			_, err2 := strconv.Atoi(parts[1])
			if err1 == nil && err2 == nil {
				// 对于范围，我们使用起始分数
				scores = append(scores, start)
			}
		}
	} else {
		// 处理单个分数
		score, err := strconv.Atoi(scoreStr)
		if err == nil {
			scores = append(scores, score)
		}
	}

	return scores
}

// GetRankByScore2024 根据省份、分数和首选科目查询2024年一分一段表排名
func GetRankByScore2024(province string, score int, subjectType string) int {
	table, exists := scoreRankTables2024[province]
	if !exists {
		log.Printf("警告: 未找到省份 %s 的一分一段表数据，返回默认排名", province)
		return 1
	}

	var data []models.ScoreRankData

	// 根据首选科目选择对应的一分一段表
	if subjectType == "物理" {
		data = table.Physics
	} else if subjectType == "历史" {
		data = table.History
	} else {
		// 默认使用物理类
		data = table.Physics
	}

	// 数据为空时的异常处理
	if len(data) == 0 {
		log.Printf("警告: 省份 %s 的%s类一分一段表数据为空", province, subjectType)
		return 1 // 返回最佳排名作为默认值
	}

	// 如果分数高于最高分，返回最高排名（最佳排名）
	if score >= data[0].Score {
		return data[0].Rank
	}

	// 如果分数低于最低分，返回最低排名（最差排名）
	if score <= data[len(data)-1].Score {
		return data[len(data)-1].Rank
	}

	// 线性插值查找对应排名
	for i := 0; i < len(data)-1; i++ {
		if score <= data[i].Score && score >= data[i+1].Score {
			// 线性插值计算排名
			scoreRange := data[i].Score - data[i+1].Score
			rankRange := data[i+1].Rank - data[i].Rank

			if scoreRange == 0 {
				return data[i].Rank
			}

			scoreDiff := score - data[i+1].Score
			interpolatedRank := data[i+1].Rank - (rankRange * scoreDiff / scoreRange)

			// 确保插值结果为正数
			return ensurePositiveRank(interpolatedRank)
		}
	}

	// 理论上不应该到达这里，但作为保险返回中位排名
	log.Printf("警告：分数 %d 未找到对应区间，返回中位排名", score)
	midIndex := len(data) / 2
	return data[midIndex].Rank
}

// ensurePositiveRank 确保排名为正数，最小值为1
func ensurePositiveRank(rank int) int {
	if rank <= 0 {
		return 1 // 最好的排名是第1名
	}
	return rank
}

// GetSubjectTypeFromClassDemand 从选科要求中推断首选科目
func GetSubjectTypeFromClassDemand(classDemand string) string {
	if classDemand == "" {
		return "物理" // 默认物理类
	}

	// 如果包含物理，认为是物理类
	if contains(classDemand, "物理") || contains(classDemand, "物") {
		return "物理"
	}

	// 如果包含历史，认为是历史类
	if contains(classDemand, "历史") || contains(classDemand, "史") {
		return "历史"
	}

	// 默认返回物理类
	return "物理"
}

// contains 检查字符串是否包含子字符串
func contains(s, substr string) bool {
	return len(s) >= len(substr) &&
		(s == substr ||
			(len(s) > len(substr) &&
				(s[:len(substr)] == substr ||
					s[len(s)-len(substr):] == substr ||
					containsInMiddle(s, substr))))
}

// containsInMiddle 检查字符串中间是否包含子字符串
func containsInMiddle(s, substr string) bool {
	for i := 1; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
