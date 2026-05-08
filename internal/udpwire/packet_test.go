package udpwire

import (
	"bytes"
	"errors"
	"testing"
)

func TestClientPacketRoundTripIPv4(t *testing.T) {
	payload := []byte("cs2-payload")
	encoded, err := EncodeClient(ClientPacket{
		FlowID:  42,
		Addr:    "1.2.3.4",
		Port:    27015,
		Payload: payload,
	})
	if err != nil {
		t.Fatalf("EncodeClient: %v", err)
	}
	decoded, err := DecodeClient(encoded)
	if err != nil {
		t.Fatalf("DecodeClient: %v", err)
	}
	if decoded.FlowID != 42 || decoded.Addr != "1.2.3.4" || decoded.Port != 27015 {
		t.Fatalf("decoded header mismatch: %+v", decoded)
	}
	if !bytes.Equal(decoded.Payload, payload) {
		t.Fatalf("payload mismatch: %q", decoded.Payload)
	}
}

func TestClientPacketRoundTripDomain(t *testing.T) {
	payload := []byte{0, 1, 2, 3}
	encoded, err := EncodeClient(ClientPacket{
		FlowID:  7,
		Addr:    "example.org",
		Port:    443,
		Payload: payload,
	})
	if err != nil {
		t.Fatalf("EncodeClient: %v", err)
	}
	decoded, err := DecodeClient(encoded)
	if err != nil {
		t.Fatalf("DecodeClient: %v", err)
	}
	if decoded.FlowID != 7 || decoded.Addr != "example.org" || decoded.Port != 443 {
		t.Fatalf("decoded header mismatch: %+v", decoded)
	}
	if !bytes.Equal(decoded.Payload, payload) {
		t.Fatalf("payload mismatch: %v", decoded.Payload)
	}
}

func TestServerPacketRoundTrip(t *testing.T) {
	payload := []byte("reply")
	encoded, err := EncodeServer(ServerPacket{FlowID: 99, Payload: payload})
	if err != nil {
		t.Fatalf("EncodeServer: %v", err)
	}
	decoded, err := DecodeServer(encoded)
	if err != nil {
		t.Fatalf("DecodeServer: %v", err)
	}
	if decoded.FlowID != 99 {
		t.Fatalf("flow id mismatch: %d", decoded.FlowID)
	}
	if !bytes.Equal(decoded.Payload, payload) {
		t.Fatalf("payload mismatch: %q", decoded.Payload)
	}
}

func TestDecodeRejectsWrongType(t *testing.T) {
	encoded, err := EncodeClient(ClientPacket{FlowID: 1, Addr: "1.2.3.4", Port: 27015, Payload: []byte("x")})
	if err != nil {
		t.Fatalf("EncodeClient: %v", err)
	}
	encoded[1] = TypeServerToClient
	_, err = DecodeClient(encoded)
	if !errors.Is(err, ErrUnsupportedType) {
		t.Fatalf("expected ErrUnsupportedType, got %v", err)
	}
}

func TestDecodeRejectsTruncatedPayload(t *testing.T) {
	encoded, err := EncodeServer(ServerPacket{FlowID: 1, Payload: []byte("reply")})
	if err != nil {
		t.Fatalf("EncodeServer: %v", err)
	}
	_, err = DecodeServer(encoded[:len(encoded)-1])
	if !errors.Is(err, ErrPacketTooShort) {
		t.Fatalf("expected ErrPacketTooShort, got %v", err)
	}
}
