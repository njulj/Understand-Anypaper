package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/njulj/Understand-Anypaper/internal/workspace"
)

const (
	DefaultHost = "127.0.0.1"
	DefaultPort = 8765
)

type Manager struct {
	Workdir        string
	ExecutablePath string
	WorkspaceMgr   workspace.Manager
}

type Options struct {
	Wait         bool
	Timeout      time.Duration
	WorkspaceDir string
	SettingsPath string
}

type ServiceState struct {
	PID          int       `json:"pid"`
	Host         string    `json:"host"`
	Port         int       `json:"port"`
	APIBaseURL   string    `json:"api_base_url"`
	WorkspaceDir string    `json:"workspace_dir"`
	DatabaseURL  string    `json:"database_url"`
	DocumentsDir string    `json:"documents_dir"`
	SettingsPath string    `json:"settings_path"`
	LogPath      string    `json:"log_path"`
	StartedAt    time.Time `json:"started_at"`
}

type Status struct {
	Managed      bool          `json:"managed"`
	Running      bool          `json:"running"`
	Healthy      bool          `json:"healthy"`
	Detail       string        `json:"detail"`
	APIBaseURL   string        `json:"api_base_url"`
	WorkspaceDir string        `json:"workspace_dir,omitempty"`
	PID          int           `json:"pid,omitempty"`
	StatePath    string        `json:"state_path"`
	DatabaseURL  string        `json:"database_url,omitempty"`
	DocumentsDir string        `json:"documents_dir,omitempty"`
	CheckedAt    time.Time     `json:"checked_at"`
	State        *ServiceState `json:"state,omitempty"`
}

type CommandResult struct {
	Message string `json:"message"`
	Status  Status `json:"status"`
}

func NewManager(workdir string, executablePath string) (Manager, error) {
	wsMgr, err := workspace.NewManager()
	if err != nil {
		return Manager{}, err
	}
	return Manager{
		Workdir:        workdir,
		ExecutablePath: executablePath,
		WorkspaceMgr:   wsMgr,
	}, nil
}

func (m Manager) Init(ctx context.Context, workspaceDir string) (workspace.InitResult, error) {
	_ = ctx
	return m.WorkspaceMgr.InitWorkspace(workspaceDir, true)
}

func (m Manager) Start(ctx context.Context, options Options) (CommandResult, error) {
	ws, err := m.ensureWorkspace(options.WorkspaceDir)
	if err != nil {
		return CommandResult{}, err
	}

	status, _ := m.Status(ctx)
	if status.Healthy {
		status.WorkspaceDir = ws.Root
		return CommandResult{Message: "Local backend service is already running.", Status: status}, nil
	}

	if status.Managed && status.PID > 0 && processExists(status.PID) && !status.Healthy {
		if options.Wait {
			waited, waitErr := m.waitForHealthy(ctx, options.Timeout, ws.Root)
			if waitErr == nil {
				return CommandResult{Message: "Local backend service is already starting.", Status: waited}, nil
			}
			return CommandResult{Message: "Local backend service exists but is not healthy yet.", Status: waited}, waitErr
		}
		return CommandResult{Message: "Local backend service is already starting.", Status: status}, nil
	}

	state, err := m.startDetachedService(ws, options)
	if err != nil {
		return CommandResult{}, err
	}
	if err := m.writeState(state); err != nil {
		return CommandResult{}, err
	}

	finalStatus := Status{
		Managed:      true,
		Running:      true,
		Healthy:      false,
		Detail:       "Service process started.",
		APIBaseURL:   state.APIBaseURL,
		WorkspaceDir: state.WorkspaceDir,
		PID:          state.PID,
		StatePath:    m.WorkspaceMgr.ServiceStatePath(),
		DatabaseURL:  state.DatabaseURL,
		DocumentsDir: state.DocumentsDir,
		CheckedAt:    time.Now(),
		State:        &state,
	}
	if options.Wait {
		waited, waitErr := m.waitForHealthy(ctx, options.Timeout, ws.Root)
		if waitErr != nil {
			return CommandResult{Message: "Service process started, but health checks did not pass in time.", Status: waited}, waitErr
		}
		finalStatus = waited
	}
	return CommandResult{Message: "Local backend service started.", Status: finalStatus}, nil
}

func (m Manager) Stop(ctx context.Context) (CommandResult, error) {
	state, err := m.readState()
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return CommandResult{}, err
	}
	if state == nil {
		status, _ := m.Status(ctx)
		if status.Healthy && !status.Managed {
			status.Detail = "A local backend is responding, but it is not managed by the current service state."
			return CommandResult{Message: status.Detail, Status: status}, nil
		}
		status.Detail = "Local backend service is not running."
		return CommandResult{Message: status.Detail, Status: status}, nil
	}

	if err := terminateProcess(state.PID); err != nil {
		return CommandResult{}, fmt.Errorf("stop local backend service: %w", err)
	}
	timeout := 10 * time.Second
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if !processExists(state.PID) && !isHealthy(ctx, state.APIBaseURL) {
			break
		}
		time.Sleep(250 * time.Millisecond)
	}
	_ = os.Remove(m.WorkspaceMgr.ServiceStatePath())
	status, _ := m.Status(ctx)
	status.Detail = "Local backend service stopped."
	return CommandResult{Message: status.Detail, Status: status}, nil
}

func (m Manager) Status(ctx context.Context) (Status, error) {
	state, err := m.readState()
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return Status{}, err
	}
	status := Status{
		Managed:   state != nil,
		StatePath: m.WorkspaceMgr.ServiceStatePath(),
		CheckedAt: time.Now(),
	}
	if state != nil {
		status.APIBaseURL = state.APIBaseURL
		status.WorkspaceDir = state.WorkspaceDir
		status.PID = state.PID
		status.DatabaseURL = state.DatabaseURL
		status.DocumentsDir = state.DocumentsDir
		status.State = state
		status.Running = processExists(state.PID)
		status.Healthy = isHealthy(ctx, state.APIBaseURL)
		switch {
		case status.Healthy:
			status.Detail = "Local backend service is running and healthy."
		case status.Running:
			status.Detail = "Local backend service process exists but is not healthy yet."
		default:
			status.Detail = "Local backend service state exists but process is not running."
		}
		if !status.Running && !status.Healthy {
			_ = os.Remove(m.WorkspaceMgr.ServiceStatePath())
			status.Managed = false
			status.Detail = "Local backend service is not running."
		}
		return status, nil
	}

	status.APIBaseURL = apiBaseURL(DefaultHost, DefaultPort)
	status.Healthy = isHealthy(ctx, status.APIBaseURL)
	status.Running = status.Healthy
	if status.Healthy {
		status.Detail = "A local backend is responding, but it is not tracked by the current service state."
	} else {
		status.Detail = "Local backend service is not running."
	}
	return status, nil
}

func (m Manager) EnsureRunning(ctx context.Context, options Options) (Status, error) {
	status, err := m.Status(ctx)
	if err == nil && status.Healthy {
		return status, nil
	}
	result, startErr := m.Start(ctx, options)
	if startErr != nil {
		return result.Status, startErr
	}
	return result.Status, nil
}

func (m Manager) ensureWorkspace(path string) (workspace.Workspace, error) {
	result, err := m.WorkspaceMgr.InitWorkspace(path, strings.TrimSpace(path) != "")
	if err != nil {
		return workspace.Workspace{}, err
	}
	return result.Workspace, nil
}

func (m Manager) startDetachedService(ws workspace.Workspace, options Options) (ServiceState, error) {
	executablePath := m.ExecutablePath
	if strings.TrimSpace(executablePath) == "" {
		currentExecutable, err := os.Executable()
		if err != nil {
			return ServiceState{}, fmt.Errorf("resolve current executable: %w", err)
		}
		executablePath = currentExecutable
	}
	if err := os.MkdirAll(ws.LogsDir, 0o755); err != nil {
		return ServiceState{}, fmt.Errorf("create logs dir: %w", err)
	}
	logPath := filepath.Join(ws.LogsDir, "backend.log")
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return ServiceState{}, fmt.Errorf("open backend log file: %w", err)
	}
	defer logFile.Close()

	args := []string{
		"desktop",
		"run-backend",
		"--host", DefaultHost,
		"--port", fmt.Sprintf("%d", DefaultPort),
		"--document-store-dir", ws.DocumentsDir,
		"--database-url", ws.DatabaseURL,
	}
	settingsPath := firstNonEmpty(options.SettingsPath, m.WorkspaceMgr.DesktopSettingsPath())
	if strings.TrimSpace(settingsPath) != "" {
		args = append(args, "--settings-path", settingsPath)
	}

	cmd := exec.Command(executablePath, args...)
	cmd.Dir = firstNonEmpty(m.Workdir, ws.Root)
	cmd.Stdout = logFile
	cmd.Stderr = logFile
	cmd.Env = append(os.Environ(),
		"PAG_DOCUMENT_STORE_DIR="+ws.DocumentsDir,
		"DATABASE_URL="+ws.DatabaseURL,
		"PAG_DESKTOP_SETTINGS_PATH="+settingsPath,
		"UV_CACHE_DIR="+filepath.Join(ws.CacheDir, "uv"),
	)
	applyDetachedProcessAttributes(cmd)
	if err := cmd.Start(); err != nil {
		return ServiceState{}, fmt.Errorf("start local backend launcher: %w", err)
	}
	pid := cmd.Process.Pid
	if err := cmd.Process.Release(); err != nil {
		return ServiceState{}, fmt.Errorf("detach local backend launcher: %w", err)
	}
	return ServiceState{
		PID:          pid,
		Host:         DefaultHost,
		Port:         DefaultPort,
		APIBaseURL:   apiBaseURL(DefaultHost, DefaultPort),
		WorkspaceDir: ws.Root,
		DatabaseURL:  ws.DatabaseURL,
		DocumentsDir: ws.DocumentsDir,
		SettingsPath: settingsPath,
		LogPath:      logPath,
		StartedAt:    time.Now(),
	}, nil
}

func (m Manager) waitForHealthy(ctx context.Context, timeout time.Duration, workspaceDir string) (Status, error) {
	if timeout <= 0 {
		timeout = 30 * time.Second
	}
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		status, err := m.Status(ctx)
		if err == nil && status.Healthy {
			return status, nil
		}
		time.Sleep(500 * time.Millisecond)
	}
	status, err := m.Status(ctx)
	if err != nil {
		return Status{}, err
	}
	status.WorkspaceDir = firstNonEmpty(status.WorkspaceDir, workspaceDir)
	return status, fmt.Errorf("timed out waiting for local backend service after %s", timeout)
}

func (m Manager) readState() (*ServiceState, error) {
	data, err := os.ReadFile(m.WorkspaceMgr.ServiceStatePath())
	if err != nil {
		return nil, err
	}
	var state ServiceState
	if err := json.Unmarshal(data, &state); err != nil {
		return nil, fmt.Errorf("parse service state: %w", err)
	}
	return &state, nil
}

func (m Manager) writeState(state ServiceState) error {
	if err := os.MkdirAll(m.WorkspaceMgr.ConfigRoot(), 0o755); err != nil {
		return fmt.Errorf("create config root: %w", err)
	}
	encoded, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return fmt.Errorf("encode service state: %w", err)
	}
	return os.WriteFile(m.WorkspaceMgr.ServiceStatePath(), append(encoded, '\n'), 0o644)
}

func apiBaseURL(host string, port int) string {
	return fmt.Sprintf("http://%s:%d", host, port)
}

func isHealthy(ctx context.Context, baseURL string) bool {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(baseURL, "/")+"/health", nil)
	if err != nil {
		return false
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		return false
	}
	defer response.Body.Close()
	return response.StatusCode >= 200 && response.StatusCode < 300
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}
