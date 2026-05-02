package vp8channel

import (
	"bytes"
	"sync"
	"testing"
	"time"
)

// TestKCPLoopback runs two KCP runtimes back-to-back through an in-memory
// pipe simulating a perfect carrier. Verifies that messages survive the
// KCP layer with their boundaries intact.
func TestKCPLoopback(t *testing.T) {
	a2b := make(chan []byte, 256)
	b2a := make(chan []byte, 256)

	var bRecvMu sync.Mutex
	var bRecv [][]byte
	doneB := make(chan struct{})

	rtA, err := startKCP(a2b, nil)
	if err != nil {
		t.Fatalf("startKCP A: %v", err)
	}
	defer rtA.close()

	rtB, err := startKCP(b2a, func(msg []byte) {
		bRecvMu.Lock()
		bRecv = append(bRecv, append([]byte(nil), msg...))
		n := len(bRecv)
		bRecvMu.Unlock()
		if n == 3 {
			close(doneB)
		}
	})
	if err != nil {
		t.Fatalf("startKCP B: %v", err)
	}
	defer rtB.close()

	// Pump packets between the two runtimes.
	stop := make(chan struct{})
	defer close(stop)

	go func() {
		for {
			select {
			case <-stop:
				return
			case pkt := <-a2b:
				rtB.deliver(pkt)
			}
		}
	}()
	go func() {
		for {
			select {
			case <-stop:
				return
			case pkt := <-b2a:
				rtA.deliver(pkt)
			}
		}
	}()

	msgs := [][]byte{
		[]byte("hello"),
		bytes.Repeat([]byte("x"), 1000),
		bytes.Repeat([]byte("y"), 20000),
	}
	for _, m := range msgs {
		if err := rtA.send(m); err != nil {
			t.Fatalf("send: %v", err)
		}
	}

	select {
	case <-doneB:
	case <-time.After(5 * time.Second):
		t.Fatal("timeout waiting for messages")
	}

	bRecvMu.Lock()
	defer bRecvMu.Unlock()
	if len(bRecv) != len(msgs) {
		t.Fatalf("got %d messages, want %d", len(bRecv), len(msgs))
	}
	for i, m := range msgs {
		if !bytes.Equal(bRecv[i], m) {
			t.Errorf("msg %d mismatch: got %d bytes, want %d", i, len(bRecv[i]), len(m))
		}
	}
}

func TestVP8KeepaliveDoesNotLookLikeKCP(t *testing.T) {
	// Keepalive frames must not be mistaken for KCP packets by the receive
	// path; otherwise the KCP stack would constantly chew on garbage.
	if len(vp8Keepalive) >= 1 && vp8Keepalive[0] == kcpMagic {
		t.Errorf("keepalive collides with kcp magic byte 0x%02x", kcpMagic)
	}
}
