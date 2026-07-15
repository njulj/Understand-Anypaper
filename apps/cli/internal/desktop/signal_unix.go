//go:build !windows

package desktop

import (
	"os"
	"syscall"
)

func syscallSignalTerm() os.Signal {
	return syscall.SIGTERM
}
