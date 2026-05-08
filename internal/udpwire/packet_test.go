package udpwire

import (
	"bytes"
	"errors"
	"testing"
)

func TestClientPacketRoundTripIPv4(t *testing.T) {
	payload := []byte("cs2-payload")
	encoded, err := EncodeClient(ClientPacket{
		FlowID:       42,
		Seq:          1001,
		SentUnixNano: 123456789,
		Addr:         "1.2.3.4",
		Port:         27015,
		Payload:      payload,
	})
	if err != nil {
		t.Fatalf("EncodeClient: %v", err)
	}
	decoded, err := DecodeClient(encoded)
	if err != nil {
		t.Fatalf("DecodeClient: %v", err)
	}
	if decoded.FlowID != 42 || decoded.Seq != 1001 || decoded.SentUnixNano != 123456789 || decoded.Addr != "1.2.3.4" || decoded.Port != 27015 {
		t.Fatalf("decoded header mismatch: %+v", decoded)
	}
	if !bytes.Equal(decoded.Payload, payload) {
		t.Fatalf("payload mismatch: %q", decoded.Payload)
	}
}

func TestClientPacketRoundTripDomain(t *testing.T) {
	payload := []byte{0, 1, 2, 3}
	encoded, err := EncodeClient(ClientPacket{
		FlowID:       7,
		Seq:          9,
		SentUnixNano: 987654321,
		Addr:         "example.org",
		Port:         443,
		Payload:      payload,
	})
	if err != nil {
		t.Fatalf("EncodeClient: %v", err)
	}
	decoded, err := DecodeClient(encoded)
	if err != nil {
		t.Fatalf("DecodeClient: %v", err)
	}
	if decoded.FlowID != 7 || decoded.Seq != 9 || decoded.SentUnixNano != 987654321 || decoded.Addr != "example.org" || decoded.Port != 443 {
		t.Fatalf("decoded header mismatch: %+v", decoded)
	}
	if !bytes.Equal(decoded.Payload, payload) {
		t.Fatalf("payload mismatch: %v", decoded.Payload)
	}
}

func TestServerPacketRoundTrip(t *testing.T) {
	payload := []byte("reply")
	encoded, err := EncodeServer(ServerPacket{
		FlowID:                 99,
		Seq:                    777,
		SentUnixNano:           111222333,
		EchoClientSeq:          555,
		EchoClientSentUnixNano: 444555666,
		Payload:                payload,
	})
	if err != nil {
		t.Fatalf("EncodeServer: %v", err)
	}
	decoded, err := DecodeServer(encoded)
	if err != nil {
		t.Fatalf("DecodeServer: %v", err)
	}
	if decoded.FlowID != 99 || decoded.Seq != 777 || decoded.SentUnixNano != 111222333 || decoded.EchoClientSeq != 555 || decoded.EchoClientSentUnixNano != 444555666 {
		t.Fatalf("server header mismatch: %+v", decoded)
	}
	if !bytes.Equal(decoded.Payload, payload) {
		t.Fatalf("payload mismatch: %q", decoded.Payload)
	}
}

func TestDecodeRejectsWrongType(t *testing.T) {
	encoded, err := EncodeClient(ClientPacket{FlowID: 1, Seq: 1, SentUnixNano: 1, Addr: "1.2.3.4", Port: 27015, Payload: []byte("x")})
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
	encoded, err := EncodeServer(ServerPacket{FlowID: 1, Seq: 1, SentUnixNano: 1, Payload: []byte("reply")})
	if err != nil {
		t.Fatalf("EncodeServer: %v", err)
	}
	_, err = DecodeServer(encoded[:len(encoded)-1])
	if !errors.Is(err, ErrPacketTooShort) {
		t.Fatalf("expected ErrPacketTooShort, got %v", err)
	}
}

func TestDecodeRejectsOldVersion(t *testing.T) {
	encoded, err := EncodeServer(ServerPacket{FlowID: 1, Seq: 1, SentUnixNano: 1, Payload: []byte("reply")})
	if err != nil {
		t.Fatalf("EncodeServer: %v", err)
	}
	encoded[0] = 1
	_, err = DecodeServer(encoded)
	if !errors.Is(err, ErrUnsupportedVer) {
		t.Fatalf("expected ErrUnsupportedVer, got %v", err)
	}
}

func TestEncodeRejectsZeroSeq(t *testing.T) {
	_, err := EncodeClient(ClientPacket{FlowID: 1, Addr: "1.2.3.4", Port: 27015})
	if !errors.Is(err, ErrInvalidSeq) {
		t.Fatalf("expected ErrInvalidSeq, got %v", err)
	}
	_, err = EncodeServer(ServerPacket{FlowID: 1})
	if !errors.Is(err, ErrInvalidSeq) {
		t.Fatalf("expected ErrInvalidSeq, got %v", err)
	}
}
