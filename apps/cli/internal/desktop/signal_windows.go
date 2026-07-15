//go:build windows

package desktop

import "os"

func syscallSignalTerm() os.Signal {
	return os.Interrupt
}
