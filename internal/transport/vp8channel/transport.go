package vp8channel

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"log"
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
	dataMarker            = 0xFF
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

// vp8Keepalive is a minimal VP8 keyframe used as idle filler. It must be
// a keyframe (P-bit=0) so that the SFU/decoder has a valid reference and
// forwards subsequent frames; switching to a P-frame here causes the SFU
// to drop the entire stream until a keyframe arrives.
var vp8Keepalive = []byte{
	0x30, 0x01, 0x00, 0x9d, 0x01, 0x2a, 0x10, 0x00,
	0x10, 0x00, 0x00, 0x47, 0x08, 0x85, 0x85, 0x88,
	0x99, 0x84, 0x88, 0xfc,
}

type streamTransport struct {
	stream        carrier.VideoTrack
	track         *webrtc.TrackLocalStaticSample
	onData        func([]byte)
	outbound      chan []byte
	inbound       chan []byte
	closeCh       chan struct{}
	writerDone    chan struct{}
	dispatchDone  chan struct{}
	closed        atomic.Bool
	writerUp      atomic.Bool
	dispatchUp    atomic.Bool
	startOnce     sync.Once
	frameInterval time.Duration
	batchSize     int

	// TX metrics
	txSendCalls    atomic.Uint64 // Send() invocations
	txSendBlocked  atomic.Uint64 // Send() that had to block on full outbound
	txDataSent     atomic.Uint64 // data samples written to track
	txDataBytes    atomic.Uint64 // total bytes of data samples
	txKeepaliveTx  atomic.Uint64 // keepalive samples written
	txWriteErrors  atomic.Uint64 // WriteSample errors
	txOutboundPeak atomic.Uint64 // max observed outbound depth in window
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
		inbound:       make(chan []byte, inboundQueueSize),
		closeCh:       make(chan struct{}),
		writerDone:    make(chan struct{}),
		dispatchDone:  make(chan struct{}),
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

	p.startOnce.Do(func() {
		p.writerUp.Store(true)
		go p.writerLoop()
		p.dispatchUp.Store(true)
		go p.dispatchLoop()
	})

	return nil
}

func (p *streamTransport) Send(data []byte) error {
	if p.closed.Load() {
		return ErrTransportClosed
	}

	frame := encodeDataFrame(data)
	p.txSendCalls.Add(1)

	// Fast path
	select {
	case <-p.closeCh:
		return ErrTransportClosed
	case p.outbound <- frame:
		return nil
	default:
	}

	// Slow path: outbound full, account for the stall.
	p.txSendBlocked.Add(1)
	select {
	case <-p.closeCh:
		return ErrTransportClosed
	case p.outbound <- frame:
		return nil
	}
}

func (p *streamTransport) Close() error {
	if p.closed.CompareAndSwap(false, true) {
		close(p.closeCh)
		if p.writerUp.Load() {
			<-p.writerDone
		}
		if p.dispatchUp.Load() {
			<-p.dispatchDone
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

func (p *streamTransport) Features() transport.Features {
	return transport.Features{
		Reliable:        false,
		Ordered:         false,
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

	reportTicker := time.NewTicker(5 * time.Second)
	defer reportTicker.Stop()
	var lastCalls, lastBlocked, lastData, lastBytes, lastKa, lastErrs uint64
	reportFn := func() {
		calls := p.txSendCalls.Load()
		blocked := p.txSendBlocked.Load()
		data := p.txDataSent.Load()
		bytes := p.txDataBytes.Load()
		ka := p.txKeepaliveTx.Load()
		errs := p.txWriteErrors.Load()
		peak := p.txOutboundPeak.Swap(0)

		dCalls := calls - lastCalls
		dBlocked := blocked - lastBlocked
		dData := data - lastData
		dBytes := bytes - lastBytes
		dKa := ka - lastKa
		dErrs := errs - lastErrs

		lastCalls, lastBlocked, lastData, lastBytes, lastKa, lastErrs =
			calls, blocked, data, bytes, ka, errs

		log.Printf("vp8channel tx[5s]: send=%d blocked=%d data=%d bytes=%d ka=%d werr=%d outQ=%d/%d peak=%d kbps=%.1f",
			dCalls, dBlocked, dData, dBytes, dKa, dErrs,
			len(p.outbound), cap(p.outbound), peak,
			float64(dBytes)*8/1024/5)
	}

	for {
		select {
		case <-p.closeCh:
			reportFn()
			return
		case <-reportTicker.C:
			reportFn()
		case <-ticker.C:
			// Track outbound queue peak for diagnostics.
			if depth := uint64(len(p.outbound)); depth > p.txOutboundPeak.Load() {
				p.txOutboundPeak.Store(depth)
			}

			var sample []byte
			isData := false
			select {
			case frame := <-p.outbound:
				sample = frame
				isData = true
				idleTicks = 0
			default:
				idleTicks++
				if idleTicks < keepaliveEvery {
					continue
				}
				idleTicks = 0
				sample = vp8Keepalive
			}

			err := p.track.WriteSample(media.Sample{
				Data:     sample,
				Duration: sampleInterval,
			})
			if err != nil {
				p.txWriteErrors.Add(1)
				continue
			}

			if isData {
				p.txDataSent.Add(1)
				p.txDataBytes.Add(uint64(len(sample)))
			} else {
				p.txKeepaliveTx.Add(1)
			}
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

	// Loss / reorder metrics, reported every 5s.
	var (
		rtpPackets    uint64
		rtpLostPkts   uint64 // packets we never saw (gap size sum)
		rtpReorders   uint64 // out-of-order arrivals (small backwards jumps)
		framesTotal   uint64 // frames marked complete (Marker)
		framesValid   uint64 // delivered to dispatch
		framesInvalid uint64 // dropped because of mid-frame loss
		droppedQueue  uint64 // dropped because inbound chan was full
	)
	reportTicker := time.NewTicker(5 * time.Second)
	defer reportTicker.Stop()
	reportDone := make(chan struct{})
	go func() {
		defer close(reportDone)
		var lastPkts, lastLost, lastReorder, lastTotal, lastValid, lastInvalid, lastDropped uint64
		for {
			select {
			case <-p.closeCh:
				return
			case <-reportTicker.C:
				pkts := atomic.LoadUint64(&rtpPackets)
				lost := atomic.LoadUint64(&rtpLostPkts)
				reord := atomic.LoadUint64(&rtpReorders)
				total := atomic.LoadUint64(&framesTotal)
				valid := atomic.LoadUint64(&framesValid)
				invalid := atomic.LoadUint64(&framesInvalid)
				dropped := atomic.LoadUint64(&droppedQueue)

				dPkts := pkts - lastPkts
				dLost := lost - lastLost
				dReord := reord - lastReorder
				dTotal := total - lastTotal
				dValid := valid - lastValid
				dInvalid := invalid - lastInvalid
				dDropped := dropped - lastDropped

				lastPkts, lastLost, lastReorder = pkts, lost, reord
				lastTotal, lastValid, lastInvalid, lastDropped = total, valid, invalid, dropped

				if dPkts == 0 && dTotal == 0 {
					continue
				}
				lossPct := 0.0
				if total := dPkts + dLost; total > 0 {
					lossPct = float64(dLost) * 100 / float64(total)
				}
				frameLossPct := 0.0
				if dTotal > 0 {
					frameLossPct = float64(dInvalid) * 100 / float64(dTotal)
				}
				log.Printf("vp8channel rx[5s]: rtp=%d lost=%d (%.2f%%) reorder=%d frames=%d ok=%d bad=%d (%.2f%%) qdrop=%d inboundQ=%d/%d",
					dPkts, dLost, lossPct, dReord,
					dTotal, dValid, dInvalid, frameLossPct,
					dDropped, len(p.inbound), cap(p.inbound))
			}
		}
	}()

	for {
		n, _, err := track.Read(buf)
		if err != nil {
			<-reportDone
			return
		}
		atomic.AddUint64(&rtpPackets, 1)

		pkt := &rtp.Packet{}
		if pkt.Unmarshal(buf[:n]) != nil {
			continue
		}

		// Detect packet loss / reordering. A single missing RTP packet
		// inside a fragmented VP8 frame would otherwise silently corrupt
		// the assembled payload (and bleed into the next frame), so we
		// invalidate the current assembly and only resume on a fresh
		// start-of-partition packet.
		if haveLastSeq {
			expected := lastSeq + 1
			if pkt.SequenceNumber != expected {
				diff := int16(pkt.SequenceNumber - expected)
				if diff > 0 {
					atomic.AddUint64(&rtpLostPkts, uint64(diff))
				} else {
					atomic.AddUint64(&rtpReorders, 1)
				}
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
			atomic.AddUint64(&framesTotal, 1)
			data := extractDataFromPayload(frameBuf)
			frameBuf = frameBuf[:0]
			frameValid = false
			if data == nil {
				atomic.AddUint64(&framesInvalid, 1)
				continue
			}
			atomic.AddUint64(&framesValid, 1)
			// Copy out of the shared frame buffer before handing the
			// payload to the dispatch goroutine.
			payload := make([]byte, len(data))
			copy(payload, data)

			// Non-blocking enqueue: dropping is preferable to stalling
			// the RTP read loop, which would let pion's UDP buffer
			// overflow and cause cascading packet loss.
			select {
			case p.inbound <- payload:
			default:
				atomic.AddUint64(&droppedQueue, 1)
			}
		}
	}
}

func (p *streamTransport) dispatchLoop() {
	defer close(p.dispatchDone)
	for {
		select {
		case <-p.closeCh:
			return
		case payload := <-p.inbound:
			if p.onData != nil {
				p.onData(payload)
			}
		}
	}
}

func encodeDataFrame(data []byte) []byte {
	frame := make([]byte, 5+len(data))
	frame[0] = dataMarker
	binary.BigEndian.PutUint32(frame[1:5], uint32(len(data)))
	copy(frame[5:], data)
	return frame
}

func extractDataFromPayload(frame []byte) []byte {
	if len(frame) < 5 || frame[0] != dataMarker {
		return nil
	}
	length := binary.BigEndian.Uint32(frame[1:5])
	if len(frame) < int(5+length) {
		return nil
	}
	return frame[5 : 5+length]
}
