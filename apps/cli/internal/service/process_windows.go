//go:build windows

package service

import (
	"os"
	"os/exec"
	"strconv"
	"syscall"
)

const createNoWindow = 0x08000000

func applyDetachedProcessAttributes(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: createNoWindow}
}

func terminateProcess(pid int) error {
	command := exec.Command("taskkill", "/pid", strconv.Itoa(pid), "/t", "/f")
	return command.Run()
}

func processExists(pid int) bool {
	process, err := os.FindProcess(pid)
	if err != nil {
		return false
	}
	err = process.Signal(syscall.Signal(0))
	return err == nil
}
