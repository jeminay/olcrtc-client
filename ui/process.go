package main

import (
	"fmt"
	"os"
	"os/exec"
	"time"
)

func (p *Program) olcrtcRun() {
	log("%s - Starting olcrtc process...", time.Now().Format("2006-01-02 15:04:05"))
	if p.RunString == "" {
		log("ERROR: Run string is empty. Please configure settings first.")
		p.showError(fmt.Errorf("run string is empty - please configure settings"))
		p.MarkUncheck()
		return
	}

	var cmd *exec.Cmd
	if p.Config.Os == "windows" {
		cmd = exec.Command("cmd.exe", "/C", p.RunString)
	} else {
		cmd = exec.Command("sh", "-c", p.RunString)
	}
	p.Cmd = cmd
	err := p.Cmd.Start()
	if err != nil {
		log("ERROR: Failed to start olcrtc: %v", err)
		p.showError(err)
		p.Cmd = nil
		p.MarkUncheck()
	} else {
		log("olcrtc process started (PID: %d)", p.Cmd.Process.Pid)
		go func() {
			err = p.Cmd.Wait()
			if err != nil {
				log("olcrtc process exited with error: %v", err)
				p.MarkUncheck()
			} else {
				log("olcrtc process exited successfully")
			}
			p.Cmd = nil
		}()
	}
}

func (p *Program) olcrtcStop() {
	log("%s - Stopping olcrtc process...", time.Now().Format("2006-01-02 15:04:05"))
	if p.Cmd == nil || p.Cmd.Process == nil {
		log("WARNING: No active olcrtc process to stop")
		return
	}

	var err error
	if p.Config.Os == "windows" {
		err = p.Cmd.Process.Kill()
	} else {
		err = p.Cmd.Process.Signal(os.Interrupt)
	}
	if err != nil {
		log("ERROR: Failed to signal olcrtc: %v", err)
		p.showError(err)
		return
	}

	log("olcrtc process termination signal sent (PID: %d)", p.Cmd.Process.Pid)

	done := make(chan error, 1)
	go func() {
		done <- p.Cmd.Wait()
	}()

	select {
	case <-time.After(5 * time.Second):
		log("WARNING: Process did not exit gracefully, forcing kill...")
		if err := p.Cmd.Process.Kill(); err != nil {
			log("ERROR: Failed to kill olcrtc: %v", err)
		} else {
			log("olcrtc process forcefully killed (PID: %d)", p.Cmd.Process.Pid)
		}
	case err := <-done:
		if err != nil {
			log("olcrtc process exited with error: %v", err)
		} else {
			log("olcrtc process exited gracefully")
		}
	}
}
