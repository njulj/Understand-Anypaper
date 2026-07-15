package desktop

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestResolveCommandPrefersPackagedBackend(t *testing.T) {
	repoRoot := t.TempDir()
	backendPath := filepath.Join(repoRoot, "apps", "web", "backend", backendExecutableName())
	if err := os.MkdirAll(filepath.Dir(backendPath), 0o755); err != nil {
		t.Fatalf("create backend dir: %v", err)
	}
	if err := os.WriteFile(backendPath, []byte("backend"), 0o755); err != nil {
		t.Fatalf("write backend stub: %v", err)
	}
	if err := os.MkdirAll(filepath.Join(repoRoot, "apps", "server"), 0o755); err != nil {
		t.Fatalf("create server dir: %v", err)
	}
	if err := os.MkdirAll(filepath.Join(repoRoot, "apps", "web"), 0o755); err != nil {
		t.Fatalf("create web dir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(repoRoot, "apps", "server", "pyproject.toml"), []byte("[project]\nname='server'\n"), 0o644); err != nil {
		t.Fatalf("write pyproject: %v", err)
	}
	if err := os.WriteFile(filepath.Join(repoRoot, "apps", "web", "package.json"), []byte("{}\n"), 0o644); err != nil {
		t.Fatalf("write package.json: %v", err)
	}
	t.Setenv("PAG_DESKTOP_USE_REPO_BACKEND", "1")

	command, env, err := ResolveCommand(LaunchConfig{
		Host:       "127.0.0.1",
		Port:       8765,
		WorkingDir: repoRoot,
	})
	if err != nil {
		t.Fatalf("ResolveCommand returned error: %v", err)
	}
	if command.Mode != BackendModePackaged {
		t.Fatalf("expected packaged mode, got %s", command.Mode)
	}
	if command.Executable != backendPath {
		t.Fatalf("unexpected backend path: %s", command.Executable)
	}
	if !containsEnv(env, "DATABASE_URL=memory") {
		t.Fatalf("expected memory database env, got %v", env)
	}
}

func TestResolveCommandPrefersLocalVirtualEnvPythonBeforeUV(t *testing.T) {
	repoRoot := t.TempDir()
	serverProjectDir := filepath.Join(repoRoot, "apps", "server")
	webDir := filepath.Join(repoRoot, "apps", "web")
	venvPython := filepath.Join(serverProjectDir, ".venv", "bin", "python")
	if err := os.MkdirAll(filepath.Dir(venvPython), 0o755); err != nil {
		t.Fatalf("create venv dir: %v", err)
	}
	if err := os.MkdirAll(webDir, 0o755); err != nil {
		t.Fatalf("create web dir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(serverProjectDir, "pyproject.toml"), []byte("[project]\nname='server'\n"), 0o644); err != nil {
		t.Fatalf("write pyproject: %v", err)
	}
	if err := os.WriteFile(filepath.Join(webDir, "package.json"), []byte("{}\n"), 0o644); err != nil {
		t.Fatalf("write package.json: %v", err)
	}
	if err := os.WriteFile(venvPython, []byte("#!/bin/sh\nexit 0\n"), 0o755); err != nil {
		t.Fatalf("write fake python: %v", err)
	}

	command, _, err := ResolveCommand(LaunchConfig{
		BackendMode: BackendModeUV,
		WorkingDir:  repoRoot,
	})
	if err != nil {
		t.Fatalf("ResolveCommand returned error: %v", err)
	}
	if command.Executable != venvPython {
		t.Fatalf("expected local venv python, got %s", command.Executable)
	}
	if len(command.Args) < 2 || command.Args[0] != "-m" || command.Args[1] != "understand_anypaper.desktop_server" {
		t.Fatalf("unexpected python args: %v", command.Args)
	}
}

func TestResolveCommandFallsBackToUV(t *testing.T) {
	repoRoot := t.TempDir()
	serverProjectDir := filepath.Join(repoRoot, "apps", "server")
	webDir := filepath.Join(repoRoot, "apps", "web")
	if err := os.MkdirAll(serverProjectDir, 0o755); err != nil {
		t.Fatalf("create server dir: %v", err)
	}
	if err := os.MkdirAll(webDir, 0o755); err != nil {
		t.Fatalf("create web dir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(serverProjectDir, "pyproject.toml"), []byte("[project]\nname='server'\n"), 0o644); err != nil {
		t.Fatalf("write pyproject: %v", err)
	}
	if err := os.WriteFile(filepath.Join(webDir, "package.json"), []byte("{}\n"), 0o644); err != nil {
		t.Fatalf("write package.json: %v", err)
	}

	uvDir := filepath.Join(repoRoot, "bin")
	if err := os.MkdirAll(uvDir, 0o755); err != nil {
		t.Fatalf("create uv dir: %v", err)
	}
	uvPath := filepath.Join(uvDir, executableName("uv"))
	if err := os.WriteFile(uvPath, []byte("#!/bin/sh\nexit 0\n"), 0o755); err != nil {
		t.Fatalf("write fake uv: %v", err)
	}
	t.Setenv("PATH", uvDir+string(os.PathListSeparator)+os.Getenv("PATH"))

	command, _, err := ResolveCommand(LaunchConfig{
		BackendMode: BackendModeUV,
		WorkingDir:  repoRoot,
	})
	if err != nil {
		t.Fatalf("ResolveCommand returned error: %v", err)
	}
	if command.Mode != BackendModeUV {
		t.Fatalf("expected uv mode, got %s", command.Mode)
	}
	if !strings.Contains(strings.Join(command.Args, " "), "understand_anypaper.desktop_server") {
		t.Fatalf("unexpected uv args: %v", command.Args)
	}
}

func containsEnv(env []string, wanted string) bool {
	for _, entry := range env {
		if entry == wanted {
			return true
		}
	}
	return false
}

func executableName(base string) string {
	if runtime.GOOS == "windows" {
		return base + ".exe"
	}
	return base
}
