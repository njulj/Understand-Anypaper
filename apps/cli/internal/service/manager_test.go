package service

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/njulj/Understand-Anypaper/internal/workspace"
)

func TestInitCreatesWorkspace(t *testing.T) {
	root := t.TempDir()
	manager := Manager{
		Workdir:      root,
		WorkspaceMgr: workspace.NewManagerWithRoot(filepath.Join(root, "config")),
	}

	result, err := manager.Init(context.Background(), "")
	if err != nil {
		t.Fatalf("Init returned error: %v", err)
	}
	if result.Workspace.Root == "" {
		t.Fatal("expected workspace root to be set")
	}
}

func TestStatusDefaultsToLocalAPIBaseURL(t *testing.T) {
	root := t.TempDir()
	manager := Manager{
		Workdir:      root,
		WorkspaceMgr: workspace.NewManagerWithRoot(filepath.Join(root, "config")),
	}

	status, err := manager.Status(context.Background())
	if err != nil {
		t.Fatalf("Status returned error: %v", err)
	}
	if status.APIBaseURL != "http://127.0.0.1:8765" {
		t.Fatalf("unexpected API base URL: %s", status.APIBaseURL)
	}
}
