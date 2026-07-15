package desktop

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
)

type BackendMode string

const (
	BackendModeAuto     BackendMode = "auto"
	BackendModePackaged BackendMode = "packaged"
	BackendModeUV       BackendMode = "uv"
)

type LaunchConfig struct {
	Host              string
	Port              int
	DocumentStoreDir  string
	SettingsPath      string
	DatabaseURL       string
	BackendMode       BackendMode
	BackendExecutable string
	ServerProjectDir  string
	WorkingDir        string
	Env               []string
	Stdout            io.Writer
	Stderr            io.Writer
}

type ResolvedCommand struct {
	Mode       BackendMode `json:"mode"`
	Executable string      `json:"executable"`
	Args       []string    `json:"args"`
	Backend    string      `json:"backend,omitempty"`
}

func Launch(ctx context.Context, cfg LaunchConfig) error {
	command, env, err := ResolveCommand(cfg)
	if err != nil {
		return err
	}

	cmd := exec.CommandContext(ctx, command.Executable, command.Args...)
	cmd.Env = env
	cmd.Stdout = cfg.Stdout
	cmd.Stderr = cfg.Stderr

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start desktop backend via %s: %w", command.Mode, err)
	}

	forwardSignals(cmd.Process)
	return cmd.Wait()
}

func ResolveCommand(cfg LaunchConfig) (ResolvedCommand, []string, error) {
	if cfg.Host == "" {
		cfg.Host = "127.0.0.1"
	}
	if cfg.Port == 0 {
		cfg.Port = 8765
	}
	if cfg.BackendMode == "" {
		cfg.BackendMode = BackendModeAuto
	}

	documentsDir, err := resolveDocumentsDir(cfg)
	if err != nil {
		return ResolvedCommand{}, nil, err
	}

	env := append([]string{}, effectiveEnv(cfg.Env)...)
	env = upsertEnv(env, "DATABASE_URL", effectiveDatabaseURL(cfg.DatabaseURL))
	env = upsertEnv(env, "PAG_DOCUMENT_STORE_DIR", documentsDir)
	if cfg.SettingsPath != "" {
		env = upsertEnv(env, "PAG_DESKTOP_SETTINGS_PATH", cfg.SettingsPath)
	}

	if cfg.BackendMode != BackendModeUV {
		if executable, ok := resolvePackagedBackend(cfg); ok {
			return ResolvedCommand{
				Mode:       BackendModePackaged,
				Executable: executable,
				Args: []string{
					"--host", cfg.Host,
					"--port", fmt.Sprintf("%d", cfg.Port),
					"--document-store-dir", documentsDir,
				},
				Backend: executable,
			}, env, nil
		}
		if cfg.BackendMode == BackendModePackaged {
			return ResolvedCommand{}, nil, errors.New("packaged backend executable was requested but not found")
		}
	}

	if cfg.BackendMode == BackendModePackaged {
		return ResolvedCommand{}, nil, errors.New("packaged backend executable was requested but not found")
	}

	serverProjectDir, err := resolveServerProjectDir(cfg)
	if err != nil {
		return ResolvedCommand{}, nil, err
	}
	if pythonPath, ok := resolveVirtualEnvPython(serverProjectDir); ok {
		return ResolvedCommand{
			Mode:       BackendModeUV,
			Executable: pythonPath,
			Args: []string{
				"-m",
				"understand_anypaper.desktop_server",
				"--host", cfg.Host,
				"--port", fmt.Sprintf("%d", cfg.Port),
				"--document-store-dir", documentsDir,
			},
			Backend: pythonPath,
		}, env, nil
	}
	uvPath, err := exec.LookPath("uv")
	if err != nil {
		return ResolvedCommand{}, nil, errors.New("could not find a packaged backend, local venv python, or uv in PATH")
	}
	return ResolvedCommand{
		Mode:       BackendModeUV,
		Executable: uvPath,
		Args: []string{
			"run",
			"--project", serverProjectDir,
			"python",
			"-m",
			"understand_anypaper.desktop_server",
			"--host", cfg.Host,
			"--port", fmt.Sprintf("%d", cfg.Port),
			"--document-store-dir", documentsDir,
		},
		Backend: serverProjectDir,
	}, env, nil
}

func ResolvePackagedBackendPath(cfg LaunchConfig) (string, bool) {
	return resolvePackagedBackend(cfg)
}

func resolvePackagedBackend(cfg LaunchConfig) (string, bool) {
	candidates := make([]string, 0, 5)
	if value := strings.TrimSpace(cfg.BackendExecutable); value != "" {
		candidates = append(candidates, value)
	}
	if value := strings.TrimSpace(os.Getenv("PAG_DESKTOP_BACKEND_EXECUTABLE")); value != "" {
		candidates = append(candidates, value)
	}
	if exePath, err := os.Executable(); err == nil {
		exeDir := filepath.Dir(exePath)
		candidates = append(candidates,
			filepath.Join(exeDir, "backend", backendExecutableName()),
			filepath.Join(exeDir, "..", "backend", backendExecutableName()),
		)
	}
	if shouldUseRepoBackendCandidate(cfg) {
		if repoRoot, ok := findRepoRoot(firstNonEmpty(cfg.WorkingDir, mustGetwd())); ok {
			candidates = append(candidates, filepath.Join(repoRoot, "apps", "web", "backend", backendExecutableName()))
		}
	}
	for _, candidate := range candidates {
		if candidate == "" {
			continue
		}
		resolved, err := filepath.Abs(candidate)
		if err != nil {
			continue
		}
		info, err := os.Stat(resolved)
		if err != nil || info.IsDir() {
			continue
		}
		return resolved, true
	}
	return "", false
}

func shouldUseRepoBackendCandidate(cfg LaunchConfig) bool {
	if strings.TrimSpace(cfg.BackendExecutable) != "" {
		return true
	}
	return strings.TrimSpace(os.Getenv("PAG_DESKTOP_USE_REPO_BACKEND")) == "1"
}

func resolveVirtualEnvPython(serverProjectDir string) (string, bool) {
	candidates := []string{
		filepath.Join(serverProjectDir, ".venv", "bin", "python"),
		filepath.Join(serverProjectDir, ".venv", "bin", "python3"),
		filepath.Join(serverProjectDir, ".venv", "Scripts", "python.exe"),
	}
	for _, candidate := range candidates {
		info, err := os.Stat(candidate)
		if err == nil && !info.IsDir() {
			return candidate, true
		}
	}
	return "", false
}

func resolveServerProjectDir(cfg LaunchConfig) (string, error) {
	if value := strings.TrimSpace(cfg.ServerProjectDir); value != "" {
		return filepath.Abs(value)
	}
	cwd := firstNonEmpty(cfg.WorkingDir, mustGetwd())
	repoRoot, ok := findRepoRoot(cwd)
	if !ok {
		return "", errors.New("could not locate repo root for apps/server; pass --server-project-dir explicitly")
	}
	return filepath.Join(repoRoot, "apps", "server"), nil
}

func resolveDocumentsDir(cfg LaunchConfig) (string, error) {
	if value := strings.TrimSpace(cfg.DocumentStoreDir); value != "" {
		return filepath.Abs(value)
	}
	cwd := firstNonEmpty(cfg.WorkingDir, mustGetwd())
	return filepath.Abs(filepath.Join(cwd, "data", "documents"))
}

func effectiveDatabaseURL(value string) string {
	if trimmed := strings.TrimSpace(value); trimmed != "" {
		return trimmed
	}
	if fromEnv := strings.TrimSpace(os.Getenv("DATABASE_URL")); fromEnv != "" {
		return fromEnv
	}
	return "memory"
}

func effectiveEnv(env []string) []string {
	if len(env) > 0 {
		return env
	}
	return os.Environ()
}

func upsertEnv(env []string, key string, value string) []string {
	prefix := key + "="
	for index, entry := range env {
		if strings.HasPrefix(entry, prefix) {
			env[index] = prefix + value
			return env
		}
	}
	return append(env, prefix+value)
}

func forwardSignals(process *os.Process) {
	if process == nil {
		return
	}
	signals := make(chan os.Signal, 2)
	watchedSignals := []os.Signal{os.Interrupt}
	if runtime.GOOS != "windows" {
		watchedSignals = append(watchedSignals, syscallSignalTerm())
	}
	signal.Notify(signals, watchedSignals...)
	go func() {
		defer signal.Stop(signals)
		for sig := range signals {
			_ = process.Signal(sig)
		}
	}()
}

func findRepoRoot(start string) (string, bool) {
	current := start
	for {
		if current == "" {
			return "", false
		}
		serverProject := filepath.Join(current, "apps", "server", "pyproject.toml")
		webProject := filepath.Join(current, "apps", "web", "package.json")
		if isFile(serverProject) && isFile(webProject) {
			return current, true
		}
		parent := filepath.Dir(current)
		if parent == current {
			return "", false
		}
		current = parent
	}
}

func isFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

func backendExecutableName() string {
	if runtime.GOOS == "windows" {
		return "server.exe"
	}
	return "server"
}

func mustGetwd() string {
	cwd, err := os.Getwd()
	if err != nil {
		return "."
	}
	return cwd
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}
