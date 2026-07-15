package workspace

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

const (
	appDirName          = "Understand Anypaper"
	configFileName      = "config.json"
	workspaceDBFileName = "uap.sqlite"
	workspaceSettings   = "settings.json"
)

type Manager struct {
	configRoot string
}

type GlobalConfig struct {
	DefaultWorkspace string   `json:"default_workspace"`
	RecentWorkspaces []string `json:"recent_workspaces"`
}

type Workspace struct {
	Root         string `json:"root"`
	DatabasePath string `json:"database_path"`
	DatabaseURL  string `json:"database_url"`
	DocumentsDir string `json:"documents_dir"`
	CacheDir     string `json:"cache_dir"`
	LogsDir      string `json:"logs_dir"`
	SettingsPath string `json:"settings_path"`
}

type InitResult struct {
	Workspace Workspace    `json:"workspace"`
	Config    GlobalConfig `json:"config"`
	Created   bool         `json:"created"`
}

func NewManager() (Manager, error) {
	if override := strings.TrimSpace(os.Getenv("PAG_CONFIG_ROOT")); override != "" {
		return NewManagerWithRoot(override), nil
	}
	configDir, err := os.UserConfigDir()
	if err != nil {
		return Manager{}, fmt.Errorf("resolve user config dir: %w", err)
	}
	return NewManagerWithRoot(filepath.Join(configDir, appDirName)), nil
}

func NewManagerWithRoot(root string) Manager {
	return Manager{configRoot: root}
}

func (m Manager) ConfigRoot() string {
	return m.configRoot
}

func (m Manager) ConfigPath() string {
	return filepath.Join(m.configRoot, configFileName)
}

func (m Manager) DefaultWorkspaceRoot() string {
	return filepath.Join(m.configRoot, "workspaces", "default")
}

func (m Manager) DesktopSettingsPath() string {
	return filepath.Join(m.configRoot, "desktop-api-config.json")
}

func (m Manager) ServiceStatePath() string {
	return filepath.Join(m.configRoot, "service.json")
}

func (m Manager) LoadConfig() (GlobalConfig, error) {
	path := m.ConfigPath()
	data, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return GlobalConfig{}, nil
		}
		return GlobalConfig{}, fmt.Errorf("read config: %w", err)
	}
	var cfg GlobalConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		return GlobalConfig{}, fmt.Errorf("parse config: %w", err)
	}
	cfg.normalize()
	return cfg, nil
}

func (m Manager) SaveConfig(cfg GlobalConfig) error {
	cfg.normalize()
	if err := os.MkdirAll(m.configRoot, 0o755); err != nil {
		return fmt.Errorf("create config root: %w", err)
	}
	encoded, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return fmt.Errorf("encode config: %w", err)
	}
	return os.WriteFile(m.ConfigPath(), append(encoded, '\n'), 0o644)
}

func (m Manager) ResolveWorkspace(path string) (Workspace, error) {
	if strings.TrimSpace(path) != "" {
		return NormalizeWorkspace(path)
	}
	cfg, err := m.LoadConfig()
	if err != nil {
		return Workspace{}, err
	}
	root := cfg.DefaultWorkspace
	if strings.TrimSpace(root) == "" {
		root = m.DefaultWorkspaceRoot()
	}
	return NormalizeWorkspace(root)
}

func (m Manager) InitWorkspace(path string, setDefault bool) (InitResult, error) {
	workspacePath := strings.TrimSpace(path)
	if workspacePath == "" {
		cfg, err := m.LoadConfig()
		if err != nil {
			return InitResult{}, err
		}
		workspacePath = cfg.DefaultWorkspace
		if strings.TrimSpace(workspacePath) == "" {
			workspacePath = m.DefaultWorkspaceRoot()
		}
	}

	ws, err := NormalizeWorkspace(workspacePath)
	if err != nil {
		return InitResult{}, err
	}
	created, err := ensureWorkspaceFiles(ws)
	if err != nil {
		return InitResult{}, err
	}

	cfg, err := m.LoadConfig()
	if err != nil {
		return InitResult{}, err
	}
	if setDefault || strings.TrimSpace(cfg.DefaultWorkspace) == "" {
		cfg.DefaultWorkspace = ws.Root
	}
	cfg.RecentWorkspaces = append(cfg.RecentWorkspaces, ws.Root)
	cfg.normalize()
	if err := m.SaveConfig(cfg); err != nil {
		return InitResult{}, err
	}
	return InitResult{Workspace: ws, Config: cfg, Created: created}, nil
}

func NormalizeWorkspace(root string) (Workspace, error) {
	if strings.TrimSpace(root) == "" {
		return Workspace{}, errors.New("workspace path cannot be empty")
	}
	absRoot, err := filepath.Abs(root)
	if err != nil {
		return Workspace{}, fmt.Errorf("resolve workspace path: %w", err)
	}
	databasePath := filepath.Join(absRoot, workspaceDBFileName)
	return Workspace{
		Root:         absRoot,
		DatabasePath: databasePath,
		DatabaseURL:  "sqlite:///" + filepath.ToSlash(databasePath),
		DocumentsDir: filepath.Join(absRoot, "documents"),
		CacheDir:     filepath.Join(absRoot, "cache"),
		LogsDir:      filepath.Join(absRoot, "logs"),
		SettingsPath: filepath.Join(absRoot, workspaceSettings),
	}, nil
}

func ensureWorkspaceFiles(ws Workspace) (bool, error) {
	dirs := []string{ws.Root, ws.DocumentsDir, ws.CacheDir, ws.LogsDir}
	for _, dir := range dirs {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return false, fmt.Errorf("create workspace dir %s: %w", dir, err)
		}
	}

	created := false
	if _, err := os.Stat(ws.DatabasePath); errors.Is(err, os.ErrNotExist) {
		if err := os.WriteFile(ws.DatabasePath, []byte{}, 0o644); err != nil {
			return false, fmt.Errorf("create sqlite file: %w", err)
		}
		created = true
	}

	if _, err := os.Stat(ws.SettingsPath); errors.Is(err, os.ErrNotExist) {
		payload := map[string]any{"workspace_version": 1}
		encoded, marshalErr := json.MarshalIndent(payload, "", "  ")
		if marshalErr != nil {
			return false, fmt.Errorf("encode workspace settings: %w", marshalErr)
		}
		if err := os.WriteFile(ws.SettingsPath, append(encoded, '\n'), 0o644); err != nil {
			return false, fmt.Errorf("create workspace settings: %w", err)
		}
		created = true
	}
	return created, nil
}

func (cfg *GlobalConfig) normalize() {
	cfg.DefaultWorkspace = strings.TrimSpace(cfg.DefaultWorkspace)
	unique := make(map[string]bool, len(cfg.RecentWorkspaces))
	normalized := make([]string, 0, len(cfg.RecentWorkspaces)+1)
	for _, item := range append([]string{cfg.DefaultWorkspace}, cfg.RecentWorkspaces...) {
		item = strings.TrimSpace(item)
		if item == "" || unique[item] {
			continue
		}
		unique[item] = true
		normalized = append(normalized, item)
	}
	if len(normalized) > 0 && cfg.DefaultWorkspace == "" {
		cfg.DefaultWorkspace = normalized[0]
	}
	if cfg.DefaultWorkspace != "" {
		sort.SliceStable(normalized, func(i, j int) bool {
			if normalized[i] == cfg.DefaultWorkspace {
				return true
			}
			if normalized[j] == cfg.DefaultWorkspace {
				return false
			}
			return normalized[i] < normalized[j]
		})
	}
	cfg.RecentWorkspaces = normalized
}
