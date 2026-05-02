package vp8channel

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
	"time"

	"github.com/openlibrecommunity/olcrtc/internal/carrier"
	"github.com/openlibrecommunity/olcrtc/internal/transport"
	"github.com/pion/rtp"
	"github.com/pion/rtp/codecs"
	"github.com/pion/webrtc/v4"
	"github.com/pion/webrtc/v4/pkg/media"
)

const (
	defaultMaxPayloadSize = 60 * 1024
	defaultConnectTimeout = 30 * time.Second
	rtpBufSize            = 65536
	outboundQueueSize     = 1024
	inboundQueueSize      = 1024
	canSendHighWatermark  = 90 // percent
	keepaliveIdlePeriod   = 100 * time.Millisecond
)

var (
	ErrVideoTrackUnsupported = errors.New("carrier does not support video tracks")
	ErrTransportClosed       = errors.New("vp8channel transport closed")
)

// vp8Keepalive is a minimal VP8 keyframe used as idle filler so that the SFU
// keeps the track flowing when KCP has nothing to send. It is never delivered
// to KCP because KCP packets always start with the convid (0xC0FFEE01 LE)
// and would never collide with this keyframe payload.
var vp8Keepalive = []byte{
	0x30, 0x01, 0x00, 0x9d, 0x01, 0x2a, 0x10, 0x00,
	0x10, 0x00, 0x00, 0x47, 0x08, 0x85, 0x85, 0x88,
	0x99, 0x84, 0x88, 0xfc,
}

// kcpMagic is the little-endian first byte of a KCP packet (low byte of
// kcpConvID = 0xC0FFEE01). Anything that does not match is treated as
// non-KCP traffic (idle keepalives, stray frames after reconnect) and
// dropped before reaching the protocol stack.
const kcpMagic = byte(0x01)

type streamTransport struct {
	stream        carrier.VideoTrack
	track         *webrtc.TrackLocalStaticSample
	onData        func([]byte)
	outbound      chan []byte
	closeCh       chan struct{}
	writerDone    chan struct{}
	closed        atomic.Bool
	writerUp      atomic.Bool
	startOnce     sync.Once
	frameInterval time.Duration
	batchSize     int

	kcp   *kcpRuntime
	kcpMu sync.RWMutex
}

func New(ctx context.Context, cfg transport.Config) (transport.Transport, error) {
	session, err := carrier.New(ctx, cfg.Carrier, carrier.Config{
		RoomURL:   cfg.RoomURL,
		Name:      cfg.Name,
		OnData:    nil,
		DNSServer: cfg.DNSServer,
		ProxyAddr: cfg.ProxyAddr,
		ProxyPort: cfg.ProxyPort,
	})
	if err != nil {
		return nil, fmt.Errorf("create provider transport: %w", err)
	}

	videoCapable, ok := session.(carrier.VideoTrackCapable)
	if !ok {
		return nil, ErrVideoTrackUnsupported
	}

	stream, err := videoCapable.OpenVideoTrack()
	if err != nil {
		return nil, fmt.Errorf("open video track: %w", err)
	}

	track, err := webrtc.NewTrackLocalStaticSample(
		webrtc.RTPCodecCapability{
			MimeType:  webrtc.MimeTypeVP8,
			ClockRate: 90000,
		},
		"vp8channel",
		"olcrtc",
	)
	if err != nil {
		return nil, fmt.Errorf("create local video track: %w", err)
	}

	fps := cfg.VP8FPS
	batchSize := cfg.VP8BatchSize

	tr := &streamTransport{
		stream:        stream,
		track:         track,
		onData:        cfg.OnData,
		outbound:      make(chan []byte, outboundQueueSize),
		closeCh:       make(chan struct{}),
		writerDone:    make(chan struct{}),
		frameInterval: time.Second / time.Duration(fps),
		batchSize:     batchSize,
	}

	if err := stream.AddTrack(track); err != nil {
		return nil, fmt.Errorf("attach local video track: %w", err)
	}
	stream.SetTrackHandler(tr.handleRemoteTrack)

	return tr, nil
}

func (p *streamTransport) Connect(ctx context.Context) error {
	connectCtx, cancel := context.WithTimeout(ctx, defaultConnectTimeout)
	defer cancel()

	if err := p.stream.Connect(connectCtx); err != nil {
		return err
	}

	var startErr error
	p.startOnce.Do(func() {
		// Start KCP first so the writerLoop has packets to forward as soon
		// as it begins ticking. KCP's own update goroutine drives keepalives
		// and ACKs once the session is up.
		rt, err := startKCP(p.outbound, p.onData)
		if err != nil {
			startErr = err
			return
		}
		p.kcpMu.Lock()
		p.kcp = rt
		p.kcpMu.Unlock()

		p.writerUp.Store(true)
		go p.writerLoop()
	})

	return startErr
}

func (p *streamTransport) Send(data []byte) error {
	if p.closed.Load() {
		return ErrTransportClosed
	}

	p.kcpMu.RLock()
	rt := p.kcp
	p.kcpMu.RUnlock()
	if rt == nil {
		return ErrTransportClosed
	}

	return rt.send(data)
}

func (p *streamTransport) Close() error {
	if p.closed.CompareAndSwap(false, true) {
		close(p.closeCh)

		p.kcpMu.RLock()
		rt := p.kcp
		p.kcpMu.RUnlock()
		if rt != nil {
			rt.close()
		}

		if p.writerUp.Load() {
			<-p.writerDone
		}
		return p.stream.Close()
	}
	return nil
}

func (p *streamTransport) drainOutbound() {
	for {
		select {
		case <-p.outbound:
		default:
			return
		}
	}
}

func (p *streamTransport) SetReconnectCallback(cb func()) {
	p.stream.SetReconnectCallback(func() {
		// Drain stale KCP segments queued for the old wire. KCP will
		// retransmit anything that mattered after the link is back up,
		// so dropping the queue here only saves us from sending obsolete
		// data that the peer would discard anyway.
		p.drainOutbound()
		if cb != nil {
			cb()
		}
	})
}

func (p *streamTransport) SetShouldReconnect(fn func() bool) {
	p.stream.SetShouldReconnect(fn)
}

func (p *streamTransport) SetEndedCallback(cb func(string)) {
	p.stream.SetEndedCallback(cb)
}

func (p *streamTransport) WatchConnection(ctx context.Context) {
	p.stream.WatchConnection(ctx)
}

func (p *streamTransport) CanSend() bool {
	return !p.closed.Load() && p.stream.CanSend() &&
		len(p.outbound) < cap(p.outbound)*canSendHighWatermark/100
}

// Features advertises reliable+ordered semantics now that KCP guarantees
// in-order delivery with retransmits. The upper layer (mux/curl tunnel)
// can rely on these properties end-to-end.
func (p *streamTransport) Features() transport.Features {
	return transport.Features{
		Reliable:        true,
		Ordered:         true,
		MessageOriented: true,
		MaxPayloadSize:  defaultMaxPayloadSize,
	}
}

func (p *streamTransport) writerLoop() {
	defer close(p.writerDone)

	// Send each sample at the wire-level rate (fps * batchSize) instead of
	// bursting batchSize samples per frame interval. Bursting makes RTP
	// timestamps disagree with wall-clock arrival, which the SFU interprets
	// as huge jitter and starts throttling the stream after a few seconds.
	sampleInterval := p.frameInterval / time.Duration(p.batchSize)
	if sampleInterval <= 0 {
		sampleInterval = p.frameInterval
	}

	ticker := time.NewTicker(sampleInterval)
	defer ticker.Stop()

	keepaliveEvery := int(keepaliveIdlePeriod / sampleInterval)
	if keepaliveEvery < 1 {
		keepaliveEvery = 1
	}
	idleTicks := 0

	for {
		select {
		case <-p.closeCh:
			return
		case <-ticker.C:
			var sample []byte
			select {
			case frame := <-p.outbound:
				sample = frame
				idleTicks = 0
			default:
				idleTicks++
				if idleTicks < keepaliveEvery {
					continue
				}
				idleTicks = 0
				sample = vp8Keepalive
			}

			_ = p.track.WriteSample(media.Sample{
				Data:     sample,
				Duration: sampleInterval,
			})
		}
	}
}

func (p *streamTransport) handleRemoteTrack(track *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
	if track.Codec().MimeType != webrtc.MimeTypeVP8 {
		go p.drainTrack(track)
		return
	}

	go p.readVP8Track(track)
}

func (p *streamTransport) drainTrack(track *webrtc.TrackRemote) {
	buf := make([]byte, rtpBufSize)
	for {
		if _, _, err := track.Read(buf); err != nil {
			return
		}
	}
}

func (p *streamTransport) readVP8Track(track *webrtc.TrackRemote) {
	var vp8Pkt codecs.VP8Packet
	var frameBuf []byte
	buf := make([]byte, rtpBufSize)

	var lastSeq uint16
	var haveLastSeq bool
	frameValid := false

	for {
		n, _, err := track.Read(buf)
		if err != nil {
			return
		}

		pkt := &rtp.Packet{}
		if pkt.Unmarshal(buf[:n]) != nil {
			continue
		}

		// Detect packet loss / reordering. A single missing RTP packet
		// inside a fragmented VP8 frame would otherwise silently corrupt
		// the assembled payload (and bleed into the next frame). KCP can
		// recover from full-frame drops, but only if the frames it does
		// receive are byte-perfect.
		if haveLastSeq {
			expected := lastSeq + 1
			if pkt.SequenceNumber != expected {
				frameValid = false
				frameBuf = frameBuf[:0]
			}
		}
		lastSeq = pkt.SequenceNumber
		haveLastSeq = true

		vp8Payload, err := vp8Pkt.Unmarshal(pkt.Payload)
		if err != nil {
			frameValid = false
			frameBuf = frameBuf[:0]
			continue
		}

		if vp8Pkt.S == 1 {
			frameBuf = frameBuf[:0]
			frameValid = true
		}

		if !frameValid {
			continue
		}

		frameBuf = append(frameBuf, vp8Payload...)

		if pkt.Marker {
			if len(frameBuf) >= 4 && frameBuf[0] == kcpMagic {
				p.kcpMu.RLock()
				rt := p.kcp
				p.kcpMu.RUnlock()
				if rt != nil {
					// Copy out of the shared frame buffer before handing
					// the payload off — KCP's deliver path is async.
					payload := make([]byte, len(frameBuf))
					copy(payload, frameBuf)
					rt.deliver(payload)
				}
			}
			frameBuf = frameBuf[:0]
			frameValid = false
		}
	}
}
