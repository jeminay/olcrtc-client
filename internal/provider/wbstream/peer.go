// Package wbstream implements the WB Stream WebRTC provider.
package wbstream

import (
	"context"
	"errors"
	"fmt"
	"log"
	"sync"
	"sync/atomic"
	"time"

	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/pion/webrtc/v4"
)

const (
	wsURL             = "wss://wbstream01-el.wb.ru:7880"
	dataTopicReliable = "olcrtc.smux"
	dataTopicLossy    = "olcrtc.udp"
)

var (
	// ErrPeerClosed is returned when an operation is attempted on a closed peer.
	ErrPeerClosed = errors.New("peer closed")
	// ErrSendQueueFull is returned when the transmission queue is full.
	ErrSendQueueFull = errors.New("send queue full")
	// ErrLiveKitNotConnected is returned when the LiveKit room is not connected.
	ErrLiveKitNotConnected = errors.New("livekit room not connected")
)

// Peer represents a WB Stream WebRTC connection using LiveKit.
type Peer struct {
	roomURL         string
	name            string
	room            *lksdk.Room
	onData          func([]byte)
	onDatagram      func([]byte)
	onReconnect     func(*webrtc.DataChannel)
	shouldReconnect func() bool
	onEnded         func(string)
	sendQueue       chan []byte
	sendCount       atomic.Uint64
	publishCount    atomic.Uint64
	recvCount       atomic.Uint64
	datagramTxCount atomic.Uint64
	datagramRxCount atomic.Uint64
	enqueueBytes    atomic.Uint64
	publishBytes    atomic.Uint64
	recvBytes       atomic.Uint64
	datagramTxBytes atomic.Uint64
	datagramRxBytes atomic.Uint64
	closed          atomic.Bool
	done            chan struct{}
	cancel          context.CancelFunc
	videoTrackMu    sync.RWMutex
	videoTracks     []webrtc.TrackLocal
	onVideoTrack    func(*webrtc.TrackRemote, *webrtc.RTPReceiver)
	wg              sync.WaitGroup
}

// NewPeer creates a new WB Stream provider peer.
func NewPeer(ctx context.Context, roomURL, name string, onData, onDatagram func([]byte)) (*Peer, error) {
	_, cancel := context.WithCancel(ctx)
	return &Peer{
		roomURL:    roomURL,
		name:       name,
		onData:     onData,
		onDatagram: onDatagram,
		sendQueue:  make(chan []byte, 5000),
		done:       make(chan struct{}),
		cancel:     cancel,
	}, nil
}

// Connect starts the WebRTC connection process.
func (p *Peer) Connect(ctx context.Context) error {
	token, err := p.getRoomToken(ctx)
	if err != nil {
		return fmt.Errorf("get room token: %w", err)
	}

	roomCB := &lksdk.RoomCallback{
		ParticipantCallback: lksdk.ParticipantCallback{
			OnDataReceived: func(data []byte, params lksdk.DataReceiveParams) {
				if params.Topic == dataTopicLossy {
					p.datagramRxCount.Add(1)
					p.datagramRxBytes.Add(uint64(len(data)))
					if p.onDatagram != nil {
						p.onDatagram(data)
					}
					return
				}

				p.recvCount.Add(1)
				p.recvBytes.Add(uint64(len(data)))
				if p.onData != nil {
					p.onData(data)
				}
			},
			OnTrackSubscribed: func(track *webrtc.TrackRemote, _ *lksdk.RemoteTrackPublication, _ *lksdk.RemoteParticipant) {
				if track.Kind() != webrtc.RTPCodecTypeVideo {
					return
				}

				p.videoTrackMu.RLock()
				cb := p.onVideoTrack
				p.videoTrackMu.RUnlock()
				if cb != nil {
					cb(track, nil)
				}
			},
		},
		OnDisconnected: func() {
			if p.onEnded != nil {
				p.onEnded("disconnected from livekit")
			}
		},
	}

	room, err := lksdk.ConnectToRoomWithToken(wsURL, token, roomCB, lksdk.WithAutoSubscribe(true), lksdk.WithICETransportPolicy(webrtc.ICETransportPolicyRelay))
	if err != nil {
		return fmt.Errorf("connect to room: %w", err)
	}

	p.room = room
	log.Printf("WB Stream data topics: reliable=%s lossy=%s", dataTopicReliable, dataTopicLossy)
	if err := p.publishPendingTracks(); err != nil {
		return err
	}
	p.wg.Add(2)
	go p.processSendQueue()
	go p.logMetrics()

	return nil
}

func (p *Peer) publishPendingTracks() error {
	p.videoTrackMu.RLock()
	defer p.videoTrackMu.RUnlock()

	for _, track := range p.videoTracks {
		if _, err := p.room.LocalParticipant.PublishTrack(track, &lksdk.TrackPublicationOptions{
			Name: "videochannel",
		}); err != nil {
			return fmt.Errorf("failed to publish track: %w", err)
		}
	}

	return nil
}

func (p *Peer) getRoomToken(ctx context.Context) (string, error) {
	accessToken, err := registerGuest(ctx, p.name)
	if err != nil {
		return "", fmt.Errorf("register guest: %w", err)
	}

	roomID := p.roomURL
	if roomID == "" || roomID == "any" {
		roomID, err = createRoom(ctx, accessToken)
		if err != nil {
			return "", fmt.Errorf("create room: %w", err)
		}
		log.Printf("WB Stream room created: %s", roomID)
		log.Printf("To connect client use: -id %s", roomID)
	}

	if err := joinRoom(ctx, accessToken, roomID); err != nil {
		return "", fmt.Errorf("join room: %w", err)
	}

	token, err := getToken(ctx, accessToken, roomID, p.name)
	if err != nil {
		return "", fmt.Errorf("get token: %w", err)
	}

	return token, nil
}

func (p *Peer) processSendQueue() {
	defer p.wg.Done()
	for {
		select {
		case <-p.done:
			return
		case data, ok := <-p.sendQueue:
			if !ok {
				return
			}
			p.publishCount.Add(1)
			p.publishBytes.Add(uint64(len(data)))
			if err := p.publishData(data, dataTopicReliable, true); err != nil {
				log.Printf("WB Stream publish data error: %v", err)
			}
		}
	}
}

func (p *Peer) publishData(data []byte, topic string, reliable bool) error {
	if p.room == nil || p.room.LocalParticipant == nil {
		return ErrLiveKitNotConnected
	}
	return p.room.LocalParticipant.PublishDataPacket(
		lksdk.UserData(data),
		lksdk.WithDataPublishTopic(topic),
		lksdk.WithDataPublishReliable(reliable),
	)
}

func (p *Peer) logMetrics() {
	defer p.wg.Done()
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	var lastEnqBytes, lastPubBytes, lastRecvBytes, lastDgTXBytes, lastDgRXBytes uint64
	var lastEnqCount, lastPubCount, lastRecvCount, lastDgTXCount, lastDgRXCount uint64
	for {
		select {
		case <-p.done:
			return
		case <-ticker.C:
			enqBytes := p.enqueueBytes.Load()
			pubBytes := p.publishBytes.Load()
			recvBytes := p.recvBytes.Load()
			dgTXBytes := p.datagramTxBytes.Load()
			dgRXBytes := p.datagramRxBytes.Load()
			enqCount := p.sendCount.Load()
			pubCount := p.publishCount.Load()
			recvCount := p.recvCount.Load()
			dgTXCount := p.datagramTxCount.Load()
			dgRXCount := p.datagramRxCount.Load()
			state := "nil"
			if p.room != nil {
				state = string(p.room.ConnectionState())
			}
			log.Printf("METRICS wb enq=%.1fKB/s pub=%.1fKB/s rx=%.1fKB/s udp_tx=%.1fKB/s udp_rx=%.1fKB/s enq_msg/s=%.1f pub_msg/s=%.1f rx_msg/s=%.1f udp_tx_msg/s=%.1f udp_rx_msg/s=%.1f queue=%d state=%s",
				float64(enqBytes-lastEnqBytes)/5.0/1024.0,
				float64(pubBytes-lastPubBytes)/5.0/1024.0,
				float64(recvBytes-lastRecvBytes)/5.0/1024.0,
				float64(dgTXBytes-lastDgTXBytes)/5.0/1024.0,
				float64(dgRXBytes-lastDgRXBytes)/5.0/1024.0,
				float64(enqCount-lastEnqCount)/5.0,
				float64(pubCount-lastPubCount)/5.0,
				float64(recvCount-lastRecvCount)/5.0,
				float64(dgTXCount-lastDgTXCount)/5.0,
				float64(dgRXCount-lastDgRXCount)/5.0,
				len(p.sendQueue),
				state,
			)
			lastEnqBytes, lastPubBytes, lastRecvBytes = enqBytes, pubBytes, recvBytes
			lastDgTXBytes, lastDgRXBytes = dgTXBytes, dgRXBytes
			lastEnqCount, lastPubCount, lastRecvCount = enqCount, pubCount, recvCount
			lastDgTXCount, lastDgRXCount = dgTXCount, dgRXCount
		}
	}
}

// Send transmits data to the room.
func (p *Peer) Send(data []byte) error {
	if p.closed.Load() {
		return ErrPeerClosed
	}
	p.sendCount.Add(1)
	p.enqueueBytes.Add(uint64(len(data)))
	select {
	case p.sendQueue <- data:
		return nil
	default:
		return ErrSendQueueFull
	}
}

// SendDatagram transmits a lossy unordered datagram through LiveKit's lossy
// DataChannel. This is used for UDP traffic and intentionally bypasses smux.
func (p *Peer) SendDatagram(data []byte) error {
	if p.closed.Load() {
		return ErrPeerClosed
	}
	if !p.CanSendDatagram() {
		return ErrLiveKitNotConnected
	}
	if err := p.publishData(data, dataTopicLossy, false); err != nil {
		return err
	}
	p.datagramTxCount.Add(1)
	p.datagramTxBytes.Add(uint64(len(data)))
	return nil
}

func (p *Peer) CanSendDatagram() bool { return p.CanSend() }

// Close terminates the provider connection.
func (p *Peer) Close() error {
	if p.closed.CompareAndSwap(false, true) {
		p.cancel()
		close(p.done)
		if p.room != nil {
			p.room.Disconnect()
		}
		close(p.sendQueue)
		p.wg.Wait()
	}
	return nil
}

// SetReconnectCallback is a stub for WB Stream.
func (p *Peer) SetReconnectCallback(cb func(*webrtc.DataChannel)) {
	p.onReconnect = cb
}

// SetShouldReconnect is a stub for WB Stream.
func (p *Peer) SetShouldReconnect(fn func() bool) {
	p.shouldReconnect = fn
}

// SetEndedCallback sets the function to call when the session ends.
func (p *Peer) SetEndedCallback(cb func(string)) {
	p.onEnded = cb
}

// WatchConnection is a stub for WB Stream.
func (p *Peer) WatchConnection(_ context.Context) {}

// CanSend checks if the provider is ready to transmit data.
func (p *Peer) CanSend() bool {
	return !p.closed.Load() && p.room != nil
}

// GetSendQueue returns the data transmission queue.
func (p *Peer) GetSendQueue() chan []byte {
	return p.sendQueue
}

// GetBufferedAmount is a stub for WB Stream.
func (p *Peer) GetBufferedAmount() uint64 {
	return 0
}

// AddVideoTrack adds a video track to the LiveKit room.
func (p *Peer) AddVideoTrack(track webrtc.TrackLocal) error {
	p.videoTrackMu.Lock()
	p.videoTracks = append(p.videoTracks, track)
	p.videoTrackMu.Unlock()

	if p.room == nil || p.room.LocalParticipant == nil {
		return nil
	}

	if _, err := p.room.LocalParticipant.PublishTrack(track, &lksdk.TrackPublicationOptions{
		Name: "videochannel",
	}); err != nil {
		return fmt.Errorf("failed to publish track: %w", err)
	}

	return nil
}

// SetVideoTrackHandler registers a callback for remote video tracks.
func (p *Peer) SetVideoTrackHandler(cb func(*webrtc.TrackRemote, *webrtc.RTPReceiver)) {
	p.videoTrackMu.Lock()
	defer p.videoTrackMu.Unlock()
	p.onVideoTrack = cb
}
