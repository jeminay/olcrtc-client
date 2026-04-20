package videochannel

import (
	"bytes"
	"testing"
)

func TestVisualRoundTrip(t *testing.T) {
	payload := []byte("hello over visual videochannel")
	frame, err := renderVisualFrame(payload, 320, 240)
	if err != nil {
		t.Fatalf("renderVisualFrame failed: %v", err)
	}

	got, err := extractVisualPayload(frame, 320, 240)
	if err != nil {
		t.Fatalf("extractVisualPayload failed: %v", err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("payload mismatch: got=%q want=%q", got, payload)
	}
}

func TestIdleFrameIgnored(t *testing.T) {
	frame, err := renderVisualFrame(nil, 320, 240)
	if err != nil {
		t.Fatalf("renderVisualFrame failed: %v", err)
	}

	got, err := extractVisualPayload(frame, 320, 240)
	if err == nil && len(got) != 0 {
		t.Fatalf("expected idle frame to be ignored, got=%q", got)
	}
}

func TestTransportFrameRoundTrip(t *testing.T) {
	encoded := encodeDataFrame(42, 0xdeadbeef, 1024, 1, 3, []byte("chunk"))
	decoded, err := decodeTransportFrame(encoded)
	if err != nil {
		t.Fatalf("decodeTransportFrame failed: %v", err)
	}
	if decoded.typ != frameTypeData || decoded.seq != 42 || decoded.crc != 0xdeadbeef {
		t.Fatalf("unexpected frame header: %+v", decoded)
	}
	if decoded.totalLen != 1024 || decoded.fragIdx != 1 || decoded.fragTotal != 3 {
		t.Fatalf("unexpected fragmentation fields: %+v", decoded)
	}
	if !bytes.Equal(decoded.payload, []byte("chunk")) {
		t.Fatalf("payload mismatch: got=%q", decoded.payload)
	}
}
