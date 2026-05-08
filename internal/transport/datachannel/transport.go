// Package datachannel provides a transport backed by the current WebRTC providers.
package datachannel

import (
	"context"
	"fmt"

	"github.com/openlibrecommunity/olcrtc/internal/carrier"
	"github.com/openlibrecommunity/olcrtc/internal/transport"
)

const defaultMaxPayloadSize = 12 * 1024

type streamTransport struct {
	stream   carrier.ByteStream
	datagram carrier.Datagram
}

// New creates a datachannel transport backed by a carrier-specific provider.
func New(ctx context.Context, cfg transport.Config) (transport.Transport, error) {
	session, err := carrier.New(ctx, cfg.Carrier, carrier.Config{
		RoomURL:    cfg.RoomURL,
		Name:       cfg.Name,
		OnData:     cfg.OnData,
		OnDatagram: cfg.OnDatagram,
		DNSServer:  cfg.DNSServer,
		ProxyAddr:  cfg.ProxyAddr,
		ProxyPort:  cfg.ProxyPort,
	})
	if err != nil {
		return nil, fmt.Errorf("create provider transport: %w", err)
	}

	streamCapable, ok := session.(carrier.ByteStreamCapable)
	if !ok {
		return nil, carrier.ErrByteStreamUnsupported
	}

	stream, err := streamCapable.OpenByteStream()
	if err != nil {
		return nil, fmt.Errorf("open byte stream: %w", err)
	}

	var datagram carrier.Datagram
	if datagramCapable, ok := session.(carrier.DatagramCapable); ok {
		datagram, _ = datagramCapable.OpenDatagram()
	}

	return &streamTransport{stream: stream, datagram: datagram}, nil
}

// Connect starts the transport connection.
func (p *streamTransport) Connect(ctx context.Context) error {
	if err := p.stream.Connect(ctx); err != nil {
		return fmt.Errorf("stream connect: %w", err)
	}
	return nil
}

// Send transmits data through the transport.
func (p *streamTransport) Send(data []byte) error {
	if err := p.stream.Send(data); err != nil {
		return fmt.Errorf("stream send: %w", err)
	}
	return nil
}

// Close terminates the transport.
func (p *streamTransport) Close() error {
	if err := p.stream.Close(); err != nil {
		return fmt.Errorf("stream close: %w", err)
	}
	return nil
}

// SetReconnectCallback registers reconnect handling.
func (p *streamTransport) SetReconnectCallback(cb func()) {
	p.stream.SetReconnectCallback(cb)
}

// SetShouldReconnect configures reconnect policy.
func (p *streamTransport) SetShouldReconnect(fn func() bool) {
	p.stream.SetShouldReconnect(fn)
}

// SetEndedCallback registers end-of-session handling.
func (p *streamTransport) SetEndedCallback(cb func(string)) {
	p.stream.SetEndedCallback(cb)
}

// WatchConnection monitors connection lifecycle.
func (p *streamTransport) WatchConnection(ctx context.Context) {
	p.stream.WatchConnection(ctx)
}

// CanSend reports whether transport is ready for sending.
func (p *streamTransport) CanSend() bool {
	return p.stream.CanSend()
}

// SendDatagram sends a lossy datagram when supported by the carrier.
func (p *streamTransport) SendDatagram(data []byte) error {
	if p.datagram == nil {
		return carrier.ErrDatagramUnsupported
	}
	if err := p.datagram.SendDatagram(data); err != nil {
		return fmt.Errorf("datagram send: %w", err)
	}
	return nil
}

// CanSendDatagram reports whether the lossy datagram path is ready.
func (p *streamTransport) CanSendDatagram() bool {
	return p.datagram != nil && p.datagram.CanSendDatagram()
}

// Features describes the current datachannel transport semantics.
func (p *streamTransport) Features() transport.Features {
	return transport.Features{
		Reliable:        true,
		Ordered:         true,
		MessageOriented: true,
		MaxPayloadSize:  defaultMaxPayloadSize,
	}
}
