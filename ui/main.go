package main

import (
	"os/exec"
	"runtime"
	"sync"

	"image/color"

	"fyne.io/fyne/v2"
	"fyne.io/fyne/v2/app"
	"fyne.io/fyne/v2/canvas"
	"fyne.io/fyne/v2/container"
	"fyne.io/fyne/v2/theme"
	"fyne.io/fyne/v2/widget"
)

type Program struct {
	App          fyne.App
	ParentWindow fyne.Window
	RunString    string
	RunCheck     *widget.Check
	Config       *Config
	Cmd          *exec.Cmd
	CmdMu        sync.Mutex
	LogsArea     *widget.RichText
	LogsText     *widget.Label
	LogsChannel  chan string
	LogsContent  string
	LogsMu       sync.Mutex
}

func main() {
	log("Starting application...")
	program := NewProgram()
	program.Run()
}

func NewProgram() *Program {
	log("Initializing program...")
	uOs := runtime.GOOS
	log("RUNTIME: Detected OS - %v", uOs)
	p := &Program{
		App:         app.New(),
		LogsChannel: make(chan string, 100),
	}
	currentProgram = p
	cfg := p.loadConfig()
	cfg.Os = uOs
	p.Config = cfg
	p.buildRunString(cfg.ConferenceID, cfg.RoomPassword, cfg.EncryptionKey, cfg.SocksPort, cfg.DNS, cfg.Provider)
	return p
}

func (p *Program) Run() {
	log("Creating main window...")
	w := p.App.NewWindow("OlcRTC")
	w.CenterOnScreen()
	w.Resize(fyne.NewSize(1280, 700))
	w.SetOnClosed(p.olcrtcStop)
	p.ParentWindow = w

	settingsBtn := widget.NewButtonWithIcon("Settings", theme.SettingsIcon(), func() {
		log("Settings button clicked")
		p.settingsWindow()
	})

	p.RunCheck = widget.NewCheck("Run", func(b bool) {
		if b {
			log("Run enabled")
			p.olcrtcRun()
		} else {
			log("Run disabled")
			p.olcrtcStop()
		}
	})

	// Create logs display area
	p.LogsText = widget.NewLabel("")
	p.LogsText.Wrapping = fyne.TextWrapWord
	logsScroll := container.NewScroll(p.LogsText)
	logsScroll.SetMinSize(fyne.NewSize(0, 300))

	// Create styled logs box with darker background
	bgRect := canvas.NewRectangle(color.NRGBA{R: 40, G: 40, B: 40, A: 255})
	logsWithPadding := container.NewBorder(
		widget.NewLabel("Logs"),
		nil, nil, nil,
		logsScroll,
	)
	logsBox := container.NewStack(
		bgRect,
		container.NewBorder(
			nil, nil, nil, nil,
			logsWithPadding,
		),
	)

	topBar := container.NewBorder(
		settingsBtn,
		p.RunCheck, nil, nil,
	)

	mainContent := container.NewVBox(
		topBar,
		logsBox,
	)

	w.SetContent(mainContent)

	go p.listenLogs()

	log("Window created and running...")
	w.ShowAndRun()
}

func (p *Program) listenLogs() {
	for logMsg := range p.LogsChannel {
		fyne.Do(func() {
			if p.LogsText != nil {
				p.LogsMu.Lock()
				p.LogsContent += logMsg + "\n"
				logsToDisplay := p.LogsContent
				p.LogsMu.Unlock()
				p.LogsText.SetText(logsToDisplay)
			}
		})
	}
}
