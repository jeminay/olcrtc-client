// Package udpwire defines the low-latency UDP datagram protocol used over
// lossy WebRTC DataChannels.
package udpwire

import (
	"encoding/binary"
	"errors"
	"fmt"
	"net"
)

const (
	// Version 2 adds per-flow sequence numbers and sender timestamps so receivers
	// can drop stale unordered datagrams and log latency without changing crypto.
	Version = 2

	TypeClientToServer = 1
	TypeServerToClient = 2

	AddrIPv4   = 1
	AddrDomain = 3
	AddrIPv6   = 4
)

var (
	ErrPacketTooShort   = errors.New("udpwire: packet too short")
	ErrUnsupportedType  = errors.New("udpwire: unsupported packet type")
	ErrUnsupportedAddr  = errors.New("udpwire: unsupported address type")
	ErrUnsupportedVer   = errors.New("udpwire: unsupported version")
	ErrInvalidFlowID    = errors.New("udpwire: invalid flow id")
	ErrInvalidSeq       = errors.New("udpwire: invalid seq")
	ErrInvalidPort      = errors.New("udpwire: invalid port")
	ErrPayloadTooLarge  = errors.New("udpwire: payload too large")
	ErrDomainTooLong    = errors.New("udpwire: domain too long")
	ErrInvalidIPAddress = errors.New("udpwire: invalid ip address")
)

type ClientPacket struct {
	FlowID       uint32
	Seq          uint64
	SentUnixNano int64
	Addr         string
	Port         int
	Payload      []byte
}

type ServerPacket struct {
	FlowID                 uint32
	Seq                    uint64
	SentUnixNano           int64
	EchoClientSeq          uint64
	EchoClientSentUnixNano int64
	Payload                []byte
}

func EncodeClient(p ClientPacket) ([]byte, error) {
	if p.FlowID == 0 {
		return nil, ErrInvalidFlowID
	}
	if p.Seq == 0 {
		return nil, ErrInvalidSeq
	}
	if p.Port < 0 || p.Port > 65535 {
		return nil, ErrInvalidPort
	}
	if len(p.Payload) > 65535 {
		return nil, ErrPayloadTooLarge
	}

	addrType, addrBytes, err := encodeAddr(p.Addr)
	if err != nil {
		return nil, err
	}

	out := make([]byte, 0, 2+4+8+8+1+len(addrBytes)+2+2+len(p.Payload))
	out = append(out, Version, TypeClientToServer)
	out = binary.BigEndian.AppendUint32(out, p.FlowID)
	out = binary.BigEndian.AppendUint64(out, p.Seq)
	out = binary.BigEndian.AppendUint64(out, uint64(p.SentUnixNano))
	out = append(out, addrType)
	out = append(out, addrBytes...)
	out = binary.BigEndian.AppendUint16(out, uint16(p.Port))
	out = binary.BigEndian.AppendUint16(out, uint16(len(p.Payload)))
	out = append(out, p.Payload...)
	return out, nil
}

func DecodeClient(packet []byte) (ClientPacket, error) {
	if len(packet) < 2+4+8+8+1+2+2 {
		return ClientPacket{}, ErrPacketTooShort
	}
	if packet[0] != Version {
		return ClientPacket{}, fmt.Errorf("%w: %d", ErrUnsupportedVer, packet[0])
	}
	if packet[1] != TypeClientToServer {
		return ClientPacket{}, fmt.Errorf("%w: %d", ErrUnsupportedType, packet[1])
	}
	flowID := binary.BigEndian.Uint32(packet[2:6])
	if flowID == 0 {
		return ClientPacket{}, ErrInvalidFlowID
	}
	seq := binary.BigEndian.Uint64(packet[6:14])
	if seq == 0 {
		return ClientPacket{}, ErrInvalidSeq
	}
	sentUnixNano := int64(binary.BigEndian.Uint64(packet[14:22]))
	off := 22
	addr, next, err := decodeAddr(packet, off)
	if err != nil {
		return ClientPacket{}, err
	}
	if len(packet) < next+4 {
		return ClientPacket{}, ErrPacketTooShort
	}
	port := int(binary.BigEndian.Uint16(packet[next : next+2]))
	payloadLen := int(binary.BigEndian.Uint16(packet[next+2 : next+4]))
	payloadStart := next + 4
	if len(packet) < payloadStart+payloadLen {
		return ClientPacket{}, ErrPacketTooShort
	}
	return ClientPacket{
		FlowID:       flowID,
		Seq:          seq,
		SentUnixNano: sentUnixNano,
		Addr:         addr,
		Port:         port,
		Payload:      append([]byte(nil), packet[payloadStart:payloadStart+payloadLen]...),
	}, nil
}

func EncodeServer(p ServerPacket) ([]byte, error) {
	if p.FlowID == 0 {
		return nil, ErrInvalidFlowID
	}
	if p.Seq == 0 {
		return nil, ErrInvalidSeq
	}
	if len(p.Payload) > 65535 {
		return nil, ErrPayloadTooLarge
	}
	out := make([]byte, 0, 2+4+8+8+8+8+2+len(p.Payload))
	out = append(out, Version, TypeServerToClient)
	out = binary.BigEndian.AppendUint32(out, p.FlowID)
	out = binary.BigEndian.AppendUint64(out, p.Seq)
	out = binary.BigEndian.AppendUint64(out, uint64(p.SentUnixNano))
	out = binary.BigEndian.AppendUint64(out, p.EchoClientSeq)
	out = binary.BigEndian.AppendUint64(out, uint64(p.EchoClientSentUnixNano))
	out = binary.BigEndian.AppendUint16(out, uint16(len(p.Payload)))
	out = append(out, p.Payload...)
	return out, nil
}

func DecodeServer(packet []byte) (ServerPacket, error) {
	if len(packet) < 2+4+8+8+8+8+2 {
		return ServerPacket{}, ErrPacketTooShort
	}
	if packet[0] != Version {
		return ServerPacket{}, fmt.Errorf("%w: %d", ErrUnsupportedVer, packet[0])
	}
	if packet[1] != TypeServerToClient {
		return ServerPacket{}, fmt.Errorf("%w: %d", ErrUnsupportedType, packet[1])
	}
	flowID := binary.BigEndian.Uint32(packet[2:6])
	if flowID == 0 {
		return ServerPacket{}, ErrInvalidFlowID
	}
	seq := binary.BigEndian.Uint64(packet[6:14])
	if seq == 0 {
		return ServerPacket{}, ErrInvalidSeq
	}
	sentUnixNano := int64(binary.BigEndian.Uint64(packet[14:22]))
	echoClientSeq := binary.BigEndian.Uint64(packet[22:30])
	echoClientSentUnixNano := int64(binary.BigEndian.Uint64(packet[30:38]))
	payloadLen := int(binary.BigEndian.Uint16(packet[38:40]))
	if len(packet) < 40+payloadLen {
		return ServerPacket{}, ErrPacketTooShort
	}
	return ServerPacket{
		FlowID:                 flowID,
		Seq:                    seq,
		SentUnixNano:           sentUnixNano,
		EchoClientSeq:          echoClientSeq,
		EchoClientSentUnixNano: echoClientSentUnixNano,
		Payload:                append([]byte(nil), packet[40:40+payloadLen]...),
	}, nil
}

func encodeAddr(addr string) (byte, []byte, error) {
	if ip := net.ParseIP(addr); ip != nil {
		if ip4 := ip.To4(); ip4 != nil {
			return AddrIPv4, append([]byte(nil), ip4...), nil
		}
		ip16 := ip.To16()
		if ip16 == nil {
			return 0, nil, ErrInvalidIPAddress
		}
		return AddrIPv6, append([]byte(nil), ip16...), nil
	}
	if len(addr) > 255 {
		return 0, nil, ErrDomainTooLong
	}
	out := make([]byte, 0, 1+len(addr))
	out = append(out, byte(len(addr)))
	out = append(out, []byte(addr)...)
	return AddrDomain, out, nil
}

func decodeAddr(packet []byte, off int) (string, int, error) {
	if len(packet) <= off {
		return "", 0, ErrPacketTooShort
	}
	switch packet[off] {
	case AddrIPv4:
		if len(packet) < off+1+4 {
			return "", 0, ErrPacketTooShort
		}
		return net.IP(packet[off+1 : off+5]).String(), off + 5, nil
	case AddrDomain:
		if len(packet) < off+2 {
			return "", 0, ErrPacketTooShort
		}
		l := int(packet[off+1])
		if len(packet) < off+2+l {
			return "", 0, ErrPacketTooShort
		}
		return string(packet[off+2 : off+2+l]), off + 2 + l, nil
	case AddrIPv6:
		if len(packet) < off+1+16 {
			return "", 0, ErrPacketTooShort
		}
		return net.IP(packet[off+1 : off+17]).String(), off + 17, nil
	default:
		return "", 0, fmt.Errorf("%w: %d", ErrUnsupportedAddr, packet[off])
	}
}
