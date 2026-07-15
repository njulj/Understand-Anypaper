//go:build !windows

package service

import (
	"os/exec"
	"syscall"
)

func applyDetachedProcessAttributes(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
}

func terminateProcess(pid int) error {
	return syscall.Kill(-pid, syscall.SIGTERM)
}

func processExists(pid int) bool {
	if pid <= 0 {
		return false
	}
	err := syscall.Kill(pid, 0)
	return err == nil
}
