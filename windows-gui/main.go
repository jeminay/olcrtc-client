package main

import (
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
)

//go:embed gui.ps1
var guiScript []byte

//go:embed assets/olcrtc.exe
var olcrtcBin []byte

//go:embed assets/sing-box.exe
var singboxBin []byte

func main() {
	root, err := runtimeDir()
	if err != nil {
		showError("runtime dir", err)
		return
	}
	if err := os.MkdirAll(root, 0o755); err != nil {
		showError("create runtime dir", err)
		return
	}

	files := map[string][]byte{
		"gui.ps1":      guiScript,
		"olcrtc.exe":   olcrtcBin,
		"sing-box.exe": singboxBin,
	}
	for name, data := range files {
		path := filepath.Join(root, name)
		if err := writeFileIfChanged(path, data); err != nil {
			showError("write "+name, err)
			return
		}
	}

	ps := filepath.Join(root, "gui.ps1")
	args := []string{
		"-NoProfile",
		"-STA",
		"-WindowStyle", "Hidden",
		"-ExecutionPolicy", "Bypass",
		"-File", ps,
		"-AppRoot", root,
	}
	cmd := exec.Command("powershell.exe", args...)
	cmd.Dir = root
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	if err := cmd.Start(); err != nil {
		showError("start PowerShell GUI", err)
		return
	}
}

func writeFileIfChanged(path string, data []byte) error {
	sum := sha256.Sum256(data)
	want := hex.EncodeToString(sum[:])
	stampPath := path + ".sha256"

	if st, err := os.Stat(path); err == nil && st.Size() == int64(len(data)) {
		if stamp, err := os.ReadFile(stampPath); err == nil && string(stamp) == want {
			return nil
		}
	}

	if err := os.WriteFile(path, data, 0o755); err != nil {
		return err
	}
	return os.WriteFile(stampPath, []byte(want), 0o644)
}

func runtimeDir() (string, error) {
	exe, err := os.Executable()
	if err != nil {
		return "", err
	}
	return filepath.Dir(exe), nil
}

func showError(step string, err error) {
	msg := fmt.Sprintf("olcRTC GUI error: %s: %v", step, err)
	_ = exec.Command("powershell.exe", "-NoProfile", "-Command", "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('"+escapePS(msg)+"','olcRTC Easy')").Run()
}

func escapePS(s string) string {
	out := ""
	for _, r := range s {
		if r == '\'' {
			out += "''"
		} else {
			out += string(r)
		}
	}
	return out
}
