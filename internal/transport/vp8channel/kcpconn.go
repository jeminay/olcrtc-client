package vp8channel

import (
	"net"
	"sync"
	"time"
)

// fakeAddr is a placeholder address used by the KCP session. The underlying
// "packet conn" is a point-to-point pipe over the VP8 carrier and has no real
// notion of an address, but kcp-go's API requires one.
var fakeAddr = &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 1}

// kcpConn is a net.PacketConn implementation that bridges kcp-go on top of
// the vp8channel byte-message carrier.
//
//	kcp.UDPSession  ──Write──▶  WriteTo  ──▶ outbound chan  ──▶ VP8 wire
//	kcp.UDPSession  ◀──Read──   ReadFrom  ◀── inbound (deliver) ◀── VP8 wire
//
// All packet boundaries are preserved by the underlying transport, which is
// exactly what KCP expects from a UDP-like conn.
type kcpConn struct {
	out       chan<- []byte
	in        chan []byte
	closed    chan struct{}
	closeOnce sync.Once

	mu        sync.Mutex
	rDeadline time.Time
	wDeadline time.Time
}

func newKCPConn(out chan<- []byte, inboundCap int) *kcpConn {
	if inboundCap <= 0 {
		inboundCap = 1024
	}
	return &kcpConn{
		out:    out,
		in:     make(chan []byte, inboundCap),
		closed: make(chan struct{}),
	}
}

// deliver hands an incoming wire payload to the KCP read loop. Drops on
// overflow are intentional — KCP will detect the loss via SACK and retransmit.
func (c *kcpConn) deliver(payload []byte) {
	cp := make([]byte, len(payload))
	copy(cp, payload)
	select {
	case c.in <- cp:
	case <-c.closed:
	default:
	}
}

func (c *kcpConn) ReadFrom(p []byte) (int, net.Addr, error) {
	c.mu.Lock()
	deadline := c.rDeadline
	c.mu.Unlock()

	var timerC <-chan time.Time
	if !deadline.IsZero() {
		d := time.Until(deadline)
		if d <= 0 {
			return 0, nil, errTimeout{}
		}
		t := time.NewTimer(d)
		defer t.Stop()
		timerC = t.C
	}

	select {
	case msg := <-c.in:
		n := copy(p, msg)
		return n, fakeAddr, nil
	case <-c.closed:
		return 0, nil, net.ErrClosed
	case <-timerC:
		return 0, nil, errTimeout{}
	}
}

func (c *kcpConn) WriteTo(p []byte, _ net.Addr) (int, error) {
	buf := make([]byte, len(p))
	copy(buf, p)

	c.mu.Lock()
	deadline := c.wDeadline
	c.mu.Unlock()

	var timerC <-chan time.Time
	if !deadline.IsZero() {
		d := time.Until(deadline)
		if d <= 0 {
			return 0, errTimeout{}
		}
		t := time.NewTimer(d)
		defer t.Stop()
		timerC = t.C
	}

	select {
	case c.out <- buf:
		return len(p), nil
	case <-c.closed:
		return 0, net.ErrClosed
	case <-timerC:
		return 0, errTimeout{}
	}
}

func (c *kcpConn) Close() error {
	c.closeOnce.Do(func() { close(c.closed) })
	return nil
}

func (c *kcpConn) LocalAddr() net.Addr { return fakeAddr }

func (c *kcpConn) SetDeadline(t time.Time) error {
	_ = c.SetReadDeadline(t)
	_ = c.SetWriteDeadline(t)
	return nil
}

func (c *kcpConn) SetReadDeadline(t time.Time) error {
	c.mu.Lock()
	c.rDeadline = t
	c.mu.Unlock()
	return nil
}

func (c *kcpConn) SetWriteDeadline(t time.Time) error {
	c.mu.Lock()
	c.wDeadline = t
	c.mu.Unlock()
	return nil
}

type errTimeout struct{}

func (errTimeout) Error() string   { return "i/o timeout" }
func (errTimeout) Timeout() bool   { return true }
func (errTimeout) Temporary() bool { return true }
