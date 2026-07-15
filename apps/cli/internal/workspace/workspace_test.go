package workspace

import (
	"os"
	"path/filepath"
	"testing"
)

func TestInitWorkspaceCreatesDefaultWorkspaceAndConfig(t *testing.T) {
	root := t.TempDir()
	manager := NewManagerWithRoot(filepath.Join(root, "config"))

	result, err := manager.InitWorkspace("", true)
	if err != nil {
		t.Fatalf("InitWorkspace returned error: %v", err)
	}
	if result.Workspace.Root != manager.DefaultWorkspaceRoot() {
		t.Fatalf("unexpected workspace root: %s", result.Workspace.Root)
	}
	if _, err := os.Stat(result.Workspace.DatabasePath); err != nil {
		t.Fatalf("expected sqlite file to exist: %v", err)
	}
	if result.Config.DefaultWorkspace != result.Workspace.Root {
		t.Fatalf("unexpected default workspace: %s", result.Config.DefaultWorkspace)
	}
}

func TestResolveWorkspaceUsesConfiguredDefault(t *testing.T) {
	root := t.TempDir()
	manager := NewManagerWithRoot(filepath.Join(root, "config"))
	expected := filepath.Join(root, "custom-workspace")
	if _, err := manager.InitWorkspace(expected, true); err != nil {
		t.Fatalf("InitWorkspace returned error: %v", err)
	}

	ws, err := manager.ResolveWorkspace("")
	if err != nil {
		t.Fatalf("ResolveWorkspace returned error: %v", err)
	}
	if ws.Root != expected {
		t.Fatalf("unexpected workspace root: %s", ws.Root)
	}
}
