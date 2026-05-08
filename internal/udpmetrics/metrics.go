// Package udpmetrics provides lightweight UDP datagram runtime metrics.
package udpmetrics

import (
	"context"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/openlibrecommunity/olcrtc/internal/logger"
)

// Metrics collects low-latency UDP datagram counters and timing samples.
type Metrics struct {
	name string

	tx          atomic.Uint64
	rx          atomic.Uint64
	dropReorder atomic.Uint64
	dropStale   atomic.Uint64

	mu  sync.Mutex
	rtt []time.Duration
	age []time.Duration
}

// Start logs a compact metrics line every five seconds until ctx is done.
func (m *Metrics) Start(ctx context.Context, name string) {
	m.name = name
	go m.loop(ctx)
}

// RecordTX records a transmitted UDP datagram.
func (m *Metrics) RecordTX() { m.tx.Add(1) }

// RecordRX records an accepted received UDP datagram.
func (m *Metrics) RecordRX() { m.rx.Add(1) }

// RecordDropReorder records a stale/out-of-order datagram drop.
func (m *Metrics) RecordDropReorder() { m.dropReorder.Add(1) }

// RecordDropStale records a stale-by-age datagram drop.
func (m *Metrics) RecordDropStale() { m.dropStale.Add(1) }

// RecordRTT records a round-trip timing sample.
func (m *Metrics) RecordRTT(d time.Duration) { m.record(&m.rtt, d) }

// RecordAge records an estimated one-way age sample. Callers should only pass
// sane values because clocks may differ between peers.
func (m *Metrics) RecordAge(d time.Duration) { m.record(&m.age, d) }

func (m *Metrics) record(dst *[]time.Duration, d time.Duration) {
	if d < 0 || d > 10*time.Second {
		return
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if len(*dst) >= 10000 {
		copy((*dst)[0:], (*dst)[len(*dst)/2:])
		*dst = (*dst)[:len(*dst)/2]
	}
	*dst = append(*dst, d)
}

func (m *Metrics) loop(ctx context.Context) {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	var lastTX, lastRX, lastDropReorder, lastDropStale uint64
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			tx := m.tx.Load()
			rx := m.rx.Load()
			dropReorder := m.dropReorder.Load()
			dropStale := m.dropStale.Load()
			rtt, age := m.drainSamples()
			logger.Infof(
				"METRICS udp-%s tx_msg/s=%.1f rx_msg/s=%.1f drop_reorder/s=%.1f drop_stale/s=%.1f rtt_ms=%s age_ms=%s",
				m.name,
				float64(tx-lastTX)/5.0,
				float64(rx-lastRX)/5.0,
				float64(dropReorder-lastDropReorder)/5.0,
				float64(dropStale-lastDropStale)/5.0,
				formatSummary(rtt),
				formatSummary(age),
			)
			lastTX, lastRX = tx, rx
			lastDropReorder, lastDropStale = dropReorder, dropStale
		}
	}
}

func (m *Metrics) drainSamples() ([]time.Duration, []time.Duration) {
	m.mu.Lock()
	defer m.mu.Unlock()
	rtt := append([]time.Duration(nil), m.rtt...)
	age := append([]time.Duration(nil), m.age...)
	m.rtt = m.rtt[:0]
	m.age = m.age[:0]
	return rtt, age
}

func formatSummary(samples []time.Duration) string {
	if len(samples) == 0 {
		return "n/a"
	}
	sort.Slice(samples, func(i, j int) bool { return samples[i] < samples[j] })
	p50 := samples[len(samples)/2]
	p95 := samples[(len(samples)*95)/100]
	if int((len(samples)*95)/100) >= len(samples) {
		p95 = samples[len(samples)-1]
	}
	max := samples[len(samples)-1]
	return "p50=" + ms(p50) + " p95=" + ms(p95) + " max=" + ms(max)
}

func ms(d time.Duration) string {
	return formatFloat(float64(d.Microseconds()) / 1000.0)
}

func formatFloat(v float64) string {
	return strconv.FormatFloat(v, 'f', 1, 64)
}
