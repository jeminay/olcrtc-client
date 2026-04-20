package carrier

import (
	"context"

	"github.com/openlibrecommunity/olcrtc/internal/provider"
	"github.com/pion/webrtc/v4"
)

// ByteStream is a carrier capability for bidirectional byte transport.
type ByteStream interface {
	Connect(ctx context.Context) error
	Send(data []byte) error
	Close() error
	SetReconnectCallback(cb func())
	SetShouldReconnect(fn func() bool)
	SetEndedCallback(cb func(string))
	WatchConnection(ctx context.Context)
	CanSend() bool
}

type legacySession struct {
	provider provider.Provider
}

// Capabilities reports the transport primitives supported by the legacy carrier.
func (s *legacySession) Capabilities() Capabilities {
	return Capabilities{ByteStream: true}
}

// OpenByteStream adapts the legacy provider to a generic byte stream capability.
func (s *legacySession) OpenByteStream() (ByteStream, error) {
	return &legacyByteStream{provider: s.provider}, nil
}

type legacyByteStream struct {
	provider provider.Provider
}

func (p *legacyByteStream) Connect(ctx context.Context) error { return p.provider.Connect(ctx) }
func (p *legacyByteStream) Send(data []byte) error            { return p.provider.Send(data) }
func (p *legacyByteStream) Close() error                      { return p.provider.Close() }

func (p *legacyByteStream) SetReconnectCallback(cb func()) {
	p.provider.SetReconnectCallback(func(_ *webrtc.DataChannel) {
		if cb != nil {
			cb()
		}
	})
}

func (p *legacyByteStream) SetShouldReconnect(fn func() bool) { p.provider.SetShouldReconnect(fn) }
func (p *legacyByteStream) SetEndedCallback(cb func(string))  { p.provider.SetEndedCallback(cb) }
func (p *legacyByteStream) WatchConnection(ctx context.Context) {
	p.provider.WatchConnection(ctx)
}
func (p *legacyByteStream) CanSend() bool { return p.provider.CanSend() }
