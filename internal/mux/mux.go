// ===========================================
// AI GENERATED / AI GENERATED / AI GENERATED
//===========================================

package mux

import (
	"encoding/binary"
	"sync"
	"time"

	"github.com/openlibrecommunity/olcrtc/internal/logger"
)

type Stream struct {
	ID         uint16
	ClientID   uint32
	recvBuf    []byte
	closed     bool
	mu         sync.Mutex
	nextSeq    uint32
	outOfOrder map[uint32][]byte
}

func (s *Stream) RecvBuf() []byte {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.recvBuf
}

type Multiplexer struct {
	streams       map[uint16]*Stream
	nextID        uint16
	clientID      uint32
	onSend        func([]byte) error
	mu            sync.RWMutex
	maxStreams    int
	maxBufferSize int
	dataReady     map[uint16]chan struct{}
	dataReadyMu   sync.Mutex
	sendSeq       map[uint16]uint32
	sendSeqMu     sync.Mutex
}

func New(clientID uint32, onSend func([]byte) error) *Multiplexer {
	return &Multiplexer{
		streams:       make(map[uint16]*Stream),
		nextID:        1,
		clientID:      clientID,
		onSend:        onSend,
		maxStreams:    10000,
		maxBufferSize: 32 * 1024 * 1024,
		dataReady:     make(map[uint16]chan struct{}),
		sendSeq:       make(map[uint16]uint32),
	}
}

func (m *Multiplexer) OpenStream() uint16 {
	m.mu.Lock()
	defer m.mu.Unlock()

	for {
		sid := m.nextID
		m.nextID++
		if m.nextID == 0 {
			m.nextID = 1
		}

		if _, exists := m.streams[sid]; !exists {
			m.streams[sid] = &Stream{
				ID:         sid,
				recvBuf:    make([]byte, 0),
				nextSeq:    0,
				outOfOrder: make(map[uint32][]byte),
			}
			return sid
		}
	}
}

func (m *Multiplexer) SendData(sid uint16, data []byte) error {
	m.mu.RLock()
	stream, exists := m.streams[sid]
	m.mu.RUnlock()

	if !exists || stream.closed {
		return nil
	}

	// Keep encrypted DataChannel messages below Telemost's observed 8 KiB cap.
	const chunkSize = 7000
	totalChunks := (len(data) + chunkSize - 1) / chunkSize

	if totalChunks > 10 {
		logger.Debug("SendData: sid=%d, size=%d bytes, chunks=%d", sid, len(data), totalChunks)
	}

	for i := 0; i < len(data); i += chunkSize {
		end := i + chunkSize
		if end > len(data) {
			end = len(data)
		}

		chunk := data[i:end]

		m.sendSeqMu.Lock()
		seq := m.sendSeq[sid]
		m.sendSeq[sid]++
		m.sendSeqMu.Unlock()

		frame := make([]byte, 12+len(chunk))
		binary.BigEndian.PutUint32(frame[0:4], m.clientID)
		binary.BigEndian.PutUint16(frame[4:6], sid)
		binary.BigEndian.PutUint16(frame[6:8], uint16(len(chunk)))
		binary.BigEndian.PutUint32(frame[8:12], seq)
		copy(frame[12:], chunk)

		if err := m.onSend(frame); err != nil {
			return err
		}
	}

	return nil
}

func (m *Multiplexer) CloseStream(sid uint16) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	if stream, exists := m.streams[sid]; exists {
		stream.closed = true
	}

	m.sendSeqMu.Lock()
	delete(m.sendSeq, sid)
	m.sendSeqMu.Unlock()

	frame := make([]byte, 12)
	binary.BigEndian.PutUint32(frame[0:4], m.clientID)
	binary.BigEndian.PutUint16(frame[4:6], sid)
	binary.BigEndian.PutUint16(frame[6:8], 0)
	binary.BigEndian.PutUint32(frame[8:12], 0)

	return m.onSend(frame)
}

func (m *Multiplexer) HandleFrame(frame []byte) {
	if len(frame) < 12 {
		if len(frame) >= 8 {
			clientID := binary.BigEndian.Uint32(frame[0:4])
			sid := binary.BigEndian.Uint16(frame[4:6])
			length := binary.BigEndian.Uint16(frame[6:8])

			if sid == 0xFFFF && length == 0xFFFF {
				m.mu.Lock()
				for streamSid, stream := range m.streams {
					if stream.ClientID == clientID {
						stream.closed = true
						delete(m.streams, streamSid)
					}
				}
				m.mu.Unlock()
			}
		}
		return
	}

	clientID := binary.BigEndian.Uint32(frame[0:4])
	sid := binary.BigEndian.Uint16(frame[4:6])
	length := binary.BigEndian.Uint16(frame[6:8])
	seq := binary.BigEndian.Uint32(frame[8:12])

	if sid == 0xFFFF && length == 0xFFFF {
		m.mu.Lock()
		for streamSid, stream := range m.streams {
			if stream.ClientID == clientID {
				stream.closed = true
				delete(m.streams, streamSid)
			}
		}
		m.mu.Unlock()
		return
	}

	if length == 0 {
		m.mu.Lock()
		if stream, exists := m.streams[sid]; exists && stream.ClientID == clientID {
			stream.closed = true
		}
		m.mu.Unlock()
		return
	}

	if len(frame) < 12+int(length) {
		return
	}

	data := frame[12 : 12+length]

	m.mu.Lock()
	defer m.mu.Unlock()

	stream, exists := m.streams[sid]
	if !exists {
		if len(m.streams) >= m.maxStreams {
			return
		}
		stream = &Stream{
			ID:         sid,
			ClientID:   clientID,
			recvBuf:    make([]byte, 0),
			nextSeq:    0,
			outOfOrder: make(map[uint32][]byte),
		}
		m.streams[sid] = stream
	} else if stream.ClientID != clientID {
		stream.ClientID = clientID
		stream.recvBuf = make([]byte, 0)
		stream.closed = false
		stream.nextSeq = 0
		stream.outOfOrder = make(map[uint32][]byte)
	}

	if seq == stream.nextSeq {
		// Backpressure: if the stream buffer is full, release the mux lock and
		// wait for the reader to drain it. Dropping/closing here would corrupt
		// the TCP stream carried over the mux — large HTTP/2 downloads (X,
		// Instagram, YouTube) that push data faster than conn.Write can accept
		// would lose bytes and hang forever.
		if s := m.waitForBufferSpace(sid, clientID, len(data)); s == nil {
			return
		} else {
			stream = s
		}
		stream.recvBuf = append(stream.recvBuf, data...)
		stream.nextSeq++

		for {
			nextData, ok := stream.outOfOrder[stream.nextSeq]
			if !ok {
				break
			}
			if s := m.waitForBufferSpace(sid, clientID, len(nextData)); s == nil {
				return
			} else {
				stream = s
			}
			nextData, ok = stream.outOfOrder[stream.nextSeq]
			if !ok {
				break
			}
			stream.recvBuf = append(stream.recvBuf, nextData...)
			delete(stream.outOfOrder, stream.nextSeq)
			stream.nextSeq++
			logger.Verbose("Applied out-of-order packet sid=%d seq=%d", sid, stream.nextSeq-1)
		}

		m.dataReadyMu.Lock()
		if ch, ok := m.dataReady[sid]; ok {
			select {
			case ch <- struct{}{}:
			default:
			}
		}
		m.dataReadyMu.Unlock()
	} else if seq > stream.nextSeq {
		if len(stream.outOfOrder) < 100 {
			stream.outOfOrder[seq] = append([]byte(nil), data...)
		}
	}
}

// waitForBufferSpace releases m.mu and waits until the stream's recvBuf has
// room for `need` more bytes, then re-acquires the lock. Returns the (possibly
// re-fetched) stream, or nil if the stream disappeared / was reset / closed.
// Caller must hold m.mu (write-locked) on entry and will hold it on return.
func (m *Multiplexer) waitForBufferSpace(sid uint16, clientID uint32, need int) *Stream {
	for {
		stream, ok := m.streams[sid]
		if !ok || stream.ClientID != clientID || stream.closed {
			return nil
		}
		if len(stream.recvBuf)+need <= m.maxBufferSize {
			return stream
		}
		m.mu.Unlock()
		time.Sleep(5 * time.Millisecond)
		m.mu.Lock()
	}
}

func (m *Multiplexer) ReadStream(sid uint16) []byte {
	m.mu.Lock()
	defer m.mu.Unlock()

	stream, exists := m.streams[sid]
	if !exists || len(stream.recvBuf) == 0 {
		return nil
	}

	data := stream.recvBuf
	stream.recvBuf = make([]byte, 0)
	return data
}

func (m *Multiplexer) StreamClosed(sid uint16) bool {
	m.mu.RLock()
	defer m.mu.RUnlock()

	stream, exists := m.streams[sid]
	return !exists || stream.closed
}

func (m *Multiplexer) GetStreams() []uint16 {
	m.mu.RLock()
	defer m.mu.RUnlock()

	sids := make([]uint16, 0, len(m.streams))
	for sid := range m.streams {
		sids = append(sids, sid)
	}
	return sids
}

func (m *Multiplexer) GetStream(sid uint16) *Stream {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.streams[sid]
}

func (m *Multiplexer) Reset() {
	m.mu.Lock()
	defer m.mu.Unlock()

	for _, stream := range m.streams {
		stream.closed = true
	}

	m.streams = make(map[uint16]*Stream)
	m.nextID = 1

	m.sendSeqMu.Lock()
	m.sendSeq = make(map[uint16]uint32)
	m.sendSeqMu.Unlock()
}

func (m *Multiplexer) UpdateSendFunc(onSend func([]byte) error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	m.onSend = onSend
}

func (m *Multiplexer) WaitForData(sid uint16) <-chan struct{} {
	m.dataReadyMu.Lock()
	defer m.dataReadyMu.Unlock()

	if _, ok := m.dataReady[sid]; !ok {
		m.dataReady[sid] = make(chan struct{}, 1)
	}
	return m.dataReady[sid]
}

func (m *Multiplexer) CleanupDataChannel(sid uint16) {
	m.dataReadyMu.Lock()
	defer m.dataReadyMu.Unlock()

	if ch, ok := m.dataReady[sid]; ok {
		close(ch)
		delete(m.dataReady, sid)
	}
}
