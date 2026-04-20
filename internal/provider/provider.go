// Package provider defines the interface and registry for different WebRTC providers.
package provider

import (
	"context"
	"errors"

	"github.com/pion/webrtc/v4"
)

var (
	ErrProviderNotFound    = errors.New("provider not found")
	ErrDataChannelTimeout  = errors.New("datachannel timeout")
	ErrDataChannelNotReady = errors.New("datachannel not ready")
	ErrSendQueueClosed     = errors.New("send queue closed")
	ErrSendQueueTimeout    = errors.New("send queue timeout")
)

// Provider defines the standard interface for WebRTC connection handlers.
type Provider interface {
	Connect(ctx context.Context) error
	Send(data []byte) error
	Close() error
	SetReconnectCallback(cb func(*webrtc.DataChannel))
	SetShouldReconnect(fn func() bool)
	SetEndedCallback(cb func(string))
	WatchConnection(ctx context.Context)
	CanSend() bool
	GetSendQueue() chan []byte
	GetBufferedAmount() uint64

	// AddVideoTrack adds a video track to the connection.
	AddVideoTrack(track *webrtc.TrackLocalStaticRTP) (*webrtc.RTPSender, error)
}

// Config holds common configuration for all providers.
type Config struct {
	RoomURL   string
	Name      string
	OnData    func([]byte)
	DNSServer string
	ProxyAddr string
	ProxyPort int
}

// Factory is a function that creates a new Provider instance.
type Factory func(ctx context.Context, cfg Config) (Provider, error)

// registry holds all registered provider factories.
var registry = make(map[string]Factory)

// Register adds a new provider factory to the registry.
func Register(name string, factory Factory) {
	registry[name] = factory
}

// New creates a new Provider instance by name.
func New(ctx context.Context, name string, cfg Config) (Provider, error) {
	factory, ok := registry[name]
	if !ok {
		return nil, ErrProviderNotFound
	}
	return factory(ctx, cfg)
}

// Available returns a list of registered provider names.
func Available() []string {
	names := make([]string, 0, len(registry))
	for name := range registry {
		names = append(names, name)
	}
	return names
}
