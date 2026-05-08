// Package client implements the local SOCKS5 client side of the olcrtc tunnel.
package client

import (
	"context"
	crand "crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"github.com/openlibrecommunity/olcrtc/internal/crypto"
	"github.com/openlibrecommunity/olcrtc/internal/link"
	"github.com/openlibrecommunity/olcrtc/internal/logger"
	"github.com/openlibrecommunity/olcrtc/internal/muxconn"
	"github.com/openlibrecommunity/olcrtc/internal/names"
	"github.com/openlibrecommunity/olcrtc/internal/udpmetrics"
	"github.com/openlibrecommunity/olcrtc/internal/udpwire"
	"github.com/xtaci/smux"
)

var (
	// ErrConnectFailed is returned when a tunnel connection fails.
	ErrConnectFailed = errors.New("tunnel connection failed")
	// ErrProxyAuth is returned when SOCKS proxy authentication fails.
	ErrProxyAuth = errors.New("SOCKS proxy auth failed")
	// ErrKeySize is returned when the encryption key is not 32 bytes.
	ErrKeySize = errors.New("key must be 32 bytes")
	// ErrInvalidSOCKSVersion is returned when the SOCKS version is not 5.
	ErrInvalidSOCKSVersion = errors.New("invalid socks version")
	// ErrUnsupportedSOCKSCommand is returned for unsupported SOCKS commands.
	ErrUnsupportedSOCKSCommand = errors.New("unsupported socks command")
	// ErrUnsupportedAddressType is returned for unsupported SOCKS address types.
	ErrUnsupportedAddressType = errors.New("unsupported address type")
	// ErrRemoteNotReady is returned when the server-side stream fails to signal readiness.
	ErrRemoteNotReady = errors.New("remote not ready")
)

// Client handles local SOCKS5 connections and tunnels them to the server.
type Client struct {
	ln         link.Link
	cipher     *crypto.Cipher
	conn       *muxconn.Conn
	session    *smux.Session
	sessMu     sync.RWMutex
	dnsServer  string
	udpFlows   sync.Map // flowID uint32 -> *udpFlow
	udpFlowID  atomic.Uint32
	udpMetrics udpmetrics.Metrics
}

type socks5Req struct {
	cmd  byte
	addr string
	port int
}

const (
	socksCmdConnect      = 0x01
	socksCmdUDPAssociate = 0x03
	udpFrameHeaderSize   = 2
	maxUDPPayload        = 65535
)

// Run starts the client with the specified parameters.
func Run(
	ctx context.Context,
	linkName,
	transportName,
	carrierName,
	roomURL,
	keyHex string,
	localAddr string,
	dnsServer,
	socksUser string,
	socksPass string,
	videoWidth int,
	videoHeight int,
	videoFPS int,
	videoBitrate string,
	videoHW string,
	videoQRSize int,
	videoQRRecovery string,
	videoCodec string,
	videoTileModule int,
	videoTileRS int,
	vp8FPS int,
	vp8BatchSize int,
) error {
	return RunWithReady(
		ctx, linkName, transportName, carrierName, roomURL, keyHex, localAddr,
		dnsServer, socksUser, socksPass, nil,
		videoWidth, videoHeight, videoFPS, videoBitrate, videoHW,
		videoQRSize, videoQRRecovery, videoCodec, videoTileModule, videoTileRS,
		vp8FPS, vp8BatchSize,
	)
}

// RunWithReady is like Run but accepts a callback that is called when the client is ready.
func RunWithReady(
	ctx context.Context,
	linkName,
	transportName,
	carrierName,
	roomURL,
	keyHex string,
	localAddr string,
	dnsServer,
	_ string,
	_ string,
	onReady func(),
	videoWidth int,
	videoHeight int,
	videoFPS int,
	videoBitrate string,
	videoHW string,
	videoQRSize int,
	videoQRRecovery string,
	videoCodec string,
	videoTileModule int,
	videoTileRS int,
	vp8FPS int,
	vp8BatchSize int,
) error {
	runCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	cipher, err := setupCipher(keyHex)
	if err != nil {
		return fmt.Errorf("setupCipher failed: %w", err)
	}

	c := &Client{cipher: cipher, dnsServer: dnsServer}
	c.initUDPFlowID()
	c.udpMetrics.Start(runCtx, "client")

	if err := c.bringUpLink(
		runCtx, linkName, transportName, carrierName, roomURL, cancel,
		dnsServer, "", 0,
		videoWidth, videoHeight, videoFPS, videoBitrate, videoHW,
		videoQRSize, videoQRRecovery, videoCodec, videoTileModule, videoTileRS,
		vp8FPS, vp8BatchSize,
	); err != nil {
		return err
	}
	defer c.shutdown()

	lc := net.ListenConfig{}
	listener, err := lc.Listen(runCtx, "tcp4", localAddr)
	if err != nil {
		return fmt.Errorf("failed to listen on %s: %w", localAddr, err)
	}
	defer func() { _ = listener.Close() }()

	logger.Infof("SOCKS5 server listening on %s", localAddr)

	if onReady != nil {
		onReady()
	}

	go c.acceptLoop(runCtx, listener)

	<-runCtx.Done()
	return nil
}

func (c *Client) initUDPFlowID() {
	var b [4]byte
	if _, err := crand.Read(b[:]); err != nil {
		c.udpFlowID.Store(uint32(time.Now().UnixNano()))
		return
	}
	c.udpFlowID.Store(binary.BigEndian.Uint32(b[:]))
}

func (c *Client) bringUpLink(
	ctx context.Context,
	linkName, transportName, carrierName, roomURL string,
	cancel context.CancelFunc,
	dnsServer, socksProxyAddr string,
	socksProxyPort int,
	videoWidth, videoHeight, videoFPS int,
	videoBitrate, videoHW string,
	videoQRSize int,
	videoQRRecovery string,
	videoCodec string,
	videoTileModule, videoTileRS int,
	vp8FPS, vp8BatchSize int,
) error {
	ln, err := link.New(ctx, linkName, link.Config{
		Transport:       transportName,
		Carrier:         carrierName,
		RoomURL:         roomURL,
		Name:            names.Generate(),
		OnData:          c.onData,
		OnDatagram:      c.onDatagram,
		DNSServer:       dnsServer,
		ProxyAddr:       socksProxyAddr,
		ProxyPort:       socksProxyPort,
		VideoWidth:      videoWidth,
		VideoHeight:     videoHeight,
		VideoFPS:        videoFPS,
		VideoBitrate:    videoBitrate,
		VideoHW:         videoHW,
		VideoQRSize:     videoQRSize,
		VideoQRRecovery: videoQRRecovery,
		VideoCodec:      videoCodec,
		VideoTileModule: videoTileModule,
		VideoTileRS:     videoTileRS,
		VP8FPS:          vp8FPS,
		VP8BatchSize:    vp8BatchSize,
	})
	if err != nil {
		return fmt.Errorf("failed to create link: %w", err)
	}
	c.ln = ln

	ln.SetEndedCallback(func(reason string) {
		logger.Infof("Client link reported conference end: %s", reason)
		cancel()
	})
	ln.SetReconnectCallback(func() { c.handleReconnect() })

	if err := ln.Connect(ctx); err != nil {
		return fmt.Errorf("failed to connect link: %w", err)
	}

	c.conn = muxconn.New(ln, c.cipher)
	sess, err := smux.Client(c.conn, smuxConfig())
	if err != nil {
		return fmt.Errorf("smux client: %w", err)
	}
	c.sessMu.Lock()
	c.session = sess
	c.sessMu.Unlock()

	go ln.WatchConnection(ctx)
	return nil
}

// smuxConfig returns the tuned smux config used on both ends.
func smuxConfig() *smux.Config {
	cfg := smux.DefaultConfig()
	cfg.Version = 2
	cfg.MaxFrameSize = 32768
	cfg.MaxReceiveBuffer = 16 * 1024 * 1024
	cfg.MaxStreamBuffer = 1024 * 1024
	cfg.KeepAliveDisabled = true
	cfg.KeepAliveInterval = 10 * time.Second
	cfg.KeepAliveTimeout = 60 * time.Second
	return cfg
}

func (c *Client) handleReconnect() {
	logger.Infof("client link reconnect - tearing down smux session")
	c.sessMu.Lock()
	if c.session != nil {
		_ = c.session.Close()
		c.session = nil
	}
	if c.conn != nil {
		_ = c.conn.Close()
		c.conn = nil
	}
	c.sessMu.Unlock()
	c.conn = muxconn.New(c.ln, c.cipher)
	sess, err := smux.Client(c.conn, smuxConfig())
	if err != nil {
		logger.Warnf("smux re-init failed: %v", err)
		return
	}
	c.sessMu.Lock()
	c.session = sess
	c.sessMu.Unlock()
}

func (c *Client) shutdown() {
	c.sessMu.Lock()
	if c.session != nil {
		_ = c.session.Close()
	}
	if c.conn != nil {
		_ = c.conn.Close()
	}
	c.sessMu.Unlock()
	if c.ln != nil {
		_ = c.ln.Close()
	}
}

func setupCipher(keyHex string) (*crypto.Cipher, error) {
	key, err := hex.DecodeString(keyHex)
	if err != nil {
		return nil, fmt.Errorf("failed to decode key: %w", err)
	}
	if len(key) != 32 {
		return nil, fmt.Errorf("%w: got %d", ErrKeySize, len(key))
	}

	cipher, err := crypto.NewCipher(string(key))
	if err != nil {
		return nil, fmt.Errorf("failed to create cipher: %w", err)
	}
	return cipher, nil
}

func (c *Client) onData(data []byte) {
	c.sessMu.RLock()
	conn := c.conn
	c.sessMu.RUnlock()
	if conn != nil {
		conn.Push(data)
	}
}

func (c *Client) onDatagram(data []byte) {
	now := time.Now()
	pt, err := c.cipher.Decrypt(data)
	if err != nil {
		return
	}
	packet, err := udpwire.DecodeServer(pt)
	if err != nil {
		logger.Warnf("udp lossy decode failed: %v", err)
		return
	}
	val, ok := c.udpFlows.Load(packet.FlowID)
	if !ok {
		return
	}
	flow := val.(*udpFlow)
	if !flow.acceptServerPacket(packet) {
		c.udpMetrics.RecordDropReorder()
		return
	}
	if packet.SentUnixNano > 0 {
		c.udpMetrics.RecordAge(now.Sub(time.Unix(0, packet.SentUnixNano)))
	}
	if packet.EchoClientSentUnixNano > 0 {
		c.udpMetrics.RecordRTT(now.Sub(time.Unix(0, packet.EchoClientSentUnixNano)))
	}
	if flow.udpConn == nil || flow.clientAddr == nil {
		return
	}
	resp := buildSocksUDP(flow.targetAddr, flow.targetPort, packet.Payload)
	_, _ = flow.udpConn.WriteToUDP(resp, flow.clientAddr)
	c.udpMetrics.RecordRX()
}

func (c *Client) datagramLink() (link.DatagramLink, bool) {
	dl, ok := c.ln.(link.DatagramLink)
	if !ok || !dl.CanSendDatagram() {
		return nil, false
	}
	return dl, true
}

func (c *Client) acceptLoop(ctx context.Context, ln net.Listener) {
	for {
		conn, err := ln.Accept()
		if err != nil {
			select {
			case <-ctx.Done():
				return
			default:
				logger.Warnf("Accept error: %v", err)
				continue
			}
		}
		go c.handleSocks5(ctx, conn)
	}
}

func (c *Client) handleSocks5(_ context.Context, conn net.Conn) {
	defer func() { _ = conn.Close() }()

	if err := c.socks5Handshake(conn); err != nil {
		logger.Warnf("SOCKS handshake failed: %v", err)
		return
	}

	req, err := c.socks5Request(conn)
	if err != nil {
		logger.Warnf("SOCKS request failed: %v", err)
		return
	}

	if req.cmd == socksCmdUDPAssociate {
		c.handleUDPAssociate(conn)
		return
	}

	logger.Infof("SOCKS request target %s:%d", req.addr, req.port)

	c.sessMu.RLock()
	sess := c.session
	c.sessMu.RUnlock()
	if sess == nil || sess.IsClosed() {
		_, _ = conn.Write(replyHostUnreachable())
		return
	}

	c.tunnel(conn, sess, req.addr, req.port)
}

func (c *Client) handleUDPAssociate(tcpConn net.Conn) {
	udpConn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 0})
	if err != nil {
		logger.Warnf("SOCKS udp associate listen failed: %v", err)
		_, _ = tcpConn.Write(replyHostUnreachable())
		return
	}
	defer func() { _ = udpConn.Close() }()

	addr := udpConn.LocalAddr().(*net.UDPAddr)
	if _, err := tcpConn.Write(replySuccessAddr(addr.IP, addr.Port)); err != nil {
		return
	}
	logger.Infof("SOCKS UDP associate listening on %s", addr.String())

	done := make(chan struct{})
	go func() {
		buf := make([]byte, 1)
		_, _ = tcpConn.Read(buf)
		close(done)
		_ = udpConn.Close()
	}()

	flows := sync.Map{}
	defer flows.Range(func(_, val any) bool {
		if flow, ok := val.(*udpFlow); ok {
			flow.close()
		}
		return true
	})
	buf := make([]byte, maxUDPPayload+512)
	for {
		_ = udpConn.SetReadDeadline(time.Now().Add(1 * time.Second))
		n, src, err := udpConn.ReadFromUDP(buf)
		if err != nil {
			select {
			case <-done:
				return
			default:
			}
			if ne, ok := err.(net.Error); ok && ne.Timeout() {
				continue
			}
			return
		}

		packet, err := parseSocksUDP(buf[:n])
		if err != nil {
			logger.Warnf("SOCKS udp parse failed: %v", err)
			continue
		}
		key := src.String() + "|" + packet.addr + ":" + fmt.Sprint(packet.port)
		val, ok := flows.Load(key)
		if !ok {
			val = c.newUDPFlow(udpConn, src, packet.addr, packet.port)
			flows.Store(key, val)
		}
		flow := val.(*udpFlow)
		if err := flow.send(packet.payload); err != nil {
			logger.Warnf("SOCKS udp send failed: %v", err)
			flows.Delete(key)
			flow.close()
		}
	}
}

type socksUDPPacket struct {
	addr    string
	port    int
	payload []byte
}

type udpFlow struct {
	client        *Client
	stream        *smux.Stream
	mu            sync.Mutex
	datagram      bool
	flowID        uint32
	udpConn       *net.UDPConn
	clientAddr    *net.UDPAddr
	targetAddr    string
	targetPort    int
	seq           atomic.Uint64
	lastServerSeq atomic.Uint64
}

func (c *Client) newUDPFlow(udpConn *net.UDPConn, clientAddr *net.UDPAddr, targetAddr string, targetPort int) *udpFlow {
	if _, ok := c.datagramLink(); ok {
		flowID := c.udpFlowID.Add(1)
		if flowID == 0 {
			flowID = c.udpFlowID.Add(1)
		}
		flow := &udpFlow{
			client:     c,
			datagram:   true,
			flowID:     flowID,
			udpConn:    udpConn,
			clientAddr: clientAddr,
			targetAddr: targetAddr,
			targetPort: targetPort,
		}
		c.udpFlows.Store(flowID, flow)
		logger.Infof("udp lossy flow=%d %s -> %s:%d", flowID, clientAddr.String(), targetAddr, targetPort)
		return flow
	}
	logger.Infof("udp lossy unavailable, falling back to smux for %s -> %s:%d", clientAddr.String(), targetAddr, targetPort)

	c.sessMu.RLock()
	sess := c.session
	c.sessMu.RUnlock()
	if sess == nil || sess.IsClosed() {
		return &udpFlow{}
	}

	stream, err := sess.OpenStream()
	if err != nil {
		logger.Warnf("udp OpenStream failed: %v", err)
		return &udpFlow{}
	}
	flow := &udpFlow{client: c, stream: stream, targetAddr: targetAddr, targetPort: targetPort}
	if err := c.sendUDPRequest(stream, targetAddr, targetPort); err != nil {
		logger.Warnf("udp connect failed: %v", err)
		_ = stream.Close()
		return &udpFlow{}
	}

	go func() {
		defer func() { _ = stream.Close() }()
		for {
			payload, err := readUDPFrame(stream)
			if err != nil {
				return
			}
			resp := buildSocksUDP(targetAddr, targetPort, payload)
			_, _ = udpConn.WriteToUDP(resp, clientAddr)
		}
	}()
	return flow
}

func (f *udpFlow) send(payload []byte) error {
	if f.datagram {
		return f.sendDatagram(payload)
	}
	if f.stream == nil {
		return ErrRemoteNotReady
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	return writeUDPFrame(f.stream, payload)
}

func (f *udpFlow) sendDatagram(payload []byte) error {
	if f.client == nil {
		return ErrRemoteNotReady
	}
	dl, ok := f.client.datagramLink()
	if !ok {
		return ErrRemoteNotReady
	}
	packet, err := udpwire.EncodeClient(udpwire.ClientPacket{
		FlowID:       f.flowID,
		Seq:          f.seq.Add(1),
		SentUnixNano: time.Now().UnixNano(),
		Addr:         f.targetAddr,
		Port:         f.targetPort,
		Payload:      payload,
	})
	if err != nil {
		return err
	}
	enc, err := f.client.cipher.Encrypt(packet)
	if err != nil {
		return err
	}
	if err := dl.SendDatagram(enc); err != nil {
		return err
	}
	f.client.udpMetrics.RecordTX()
	return nil
}

func (f *udpFlow) acceptServerPacket(packet udpwire.ServerPacket) bool {
	for {
		last := f.lastServerSeq.Load()
		if packet.Seq <= last {
			return false
		}
		if f.lastServerSeq.CompareAndSwap(last, packet.Seq) {
			return true
		}
	}
}

func (f *udpFlow) close() {
	if f.datagram && f.client != nil {
		f.client.udpFlows.Delete(f.flowID)
	}
	if f.stream != nil {
		_ = f.stream.Close()
	}
}

func (c *Client) sendUDPRequest(stream *smux.Stream, targetAddr string, targetPort int) error {
	req, err := json.Marshal(map[string]any{"cmd": "udp", "addr": targetAddr, "port": targetPort})
	if err != nil {
		return err
	}
	_ = stream.SetWriteDeadline(time.Now().Add(10 * time.Second))
	if _, err := stream.Write(req); err != nil {
		return err
	}
	_ = stream.SetWriteDeadline(time.Time{})
	ack := make([]byte, 1)
	_ = stream.SetReadDeadline(time.Now().Add(15 * time.Second))
	if _, err := io.ReadFull(stream, ack); err != nil || ack[0] != 0x00 {
		return fmt.Errorf("udp remote not ready: %w", err)
	}
	_ = stream.SetReadDeadline(time.Time{})
	return nil
}

func (c *Client) tunnel(conn net.Conn, sess *smux.Session, targetAddr string, targetPort int) {
	stream, err := sess.OpenStream()
	if err != nil {
		logger.Warnf("OpenStream failed: %v", err)
		_, _ = conn.Write(replyHostUnreachable())
		return
	}
	defer func() { _ = stream.Close() }()

	logger.Infof("sid=%d tunnel to %s:%d", stream.ID(), targetAddr, targetPort)

	if err := c.sendConnectRequest(stream, targetAddr, targetPort); err != nil {
		logger.Warnf("sid=%d connect failed: %v", stream.ID(), err)
		_, _ = conn.Write(replyHostUnreachable())
		return
	}

	if _, err := conn.Write(replySuccess()); err != nil {
		return
	}

	go func() {
		_, _ = io.Copy(stream, conn)
		_ = stream.Close()
	}()
	_, _ = io.Copy(conn, stream)
}

func (c *Client) sendConnectRequest(stream *smux.Stream, targetAddr string, targetPort int) error {
	connectReq, err := json.Marshal(map[string]any{
		"cmd":  "connect",
		"addr": targetAddr,
		"port": targetPort,
	})
	if err != nil {
		return fmt.Errorf("sid=%d marshal connect req: %w", stream.ID(), err)
	}

	_ = stream.SetWriteDeadline(time.Now().Add(10 * time.Second))
	if _, err := stream.Write(connectReq); err != nil {
		return fmt.Errorf("sid=%d write connect req: %w", stream.ID(), err)
	}
	_ = stream.SetWriteDeadline(time.Time{})

	ack := make([]byte, 1)
	_ = stream.SetReadDeadline(time.Now().Add(15 * time.Second))
	if _, err := io.ReadFull(stream, ack); err != nil || ack[0] != 0x00 {
		return fmt.Errorf("sid=%d: %w (read_err=%w ack=%v)", stream.ID(), ErrRemoteNotReady, err, ack)
	}
	_ = stream.SetReadDeadline(time.Time{})
	return nil
}

func (c *Client) socks5Handshake(conn net.Conn) error {
	buf := make([]byte, 2)
	if _, err := io.ReadFull(conn, buf); err != nil {
		return fmt.Errorf("read socks5 header: %w", err)
	}
	if buf[0] != 5 {
		return fmt.Errorf("%w: %d", ErrInvalidSOCKSVersion, buf[0])
	}
	methods := make([]byte, buf[1])
	if _, err := io.ReadFull(conn, methods); err != nil {
		return fmt.Errorf("read socks5 methods: %w", err)
	}
	if _, err := conn.Write([]byte{5, 0}); err != nil {
		return fmt.Errorf("write socks5 auth: %w", err)
	}
	return nil
}

func (c *Client) socks5Request(conn net.Conn) (socks5Req, error) {
	header := make([]byte, 4)
	if _, err := io.ReadFull(conn, header); err != nil {
		return socks5Req{}, fmt.Errorf("read socks5 request: %w", err)
	}
	if header[1] != socksCmdConnect && header[1] != socksCmdUDPAssociate {
		return socks5Req{}, fmt.Errorf("%w: %d", ErrUnsupportedSOCKSCommand, header[1])
	}

	addr, err := c.readSocks5Addr(conn, header[3])
	if err != nil {
		return socks5Req{}, err
	}

	portBuf := make([]byte, 2)
	if _, err := io.ReadFull(conn, portBuf); err != nil {
		return socks5Req{}, fmt.Errorf("read socks5 port: %w", err)
	}
	port := int(binary.BigEndian.Uint16(portBuf))

	return socks5Req{cmd: header[1], addr: addr, port: port}, nil
}

func parseSocksUDP(packet []byte) (socksUDPPacket, error) {
	if len(packet) < 10 {
		return socksUDPPacket{}, fmt.Errorf("short udp packet: %d", len(packet))
	}
	if packet[0] != 0 || packet[1] != 0 || packet[2] != 0 {
		return socksUDPPacket{}, fmt.Errorf("unsupported udp frag/header")
	}
	off := 3
	addr, next, err := parseSocksAddr(packet, off)
	if err != nil {
		return socksUDPPacket{}, err
	}
	if len(packet) < next+2 {
		return socksUDPPacket{}, fmt.Errorf("short udp port")
	}
	port := int(binary.BigEndian.Uint16(packet[next : next+2]))
	return socksUDPPacket{addr: addr, port: port, payload: append([]byte(nil), packet[next+2:]...)}, nil
}

func parseSocksAddr(packet []byte, off int) (string, int, error) {
	if len(packet) <= off {
		return "", 0, fmt.Errorf("missing atyp")
	}
	switch packet[off] {
	case 1:
		if len(packet) < off+1+4 {
			return "", 0, fmt.Errorf("short ipv4")
		}
		return net.IP(packet[off+1 : off+5]).String(), off + 5, nil
	case 3:
		if len(packet) < off+2 {
			return "", 0, fmt.Errorf("short domain len")
		}
		l := int(packet[off+1])
		if len(packet) < off+2+l {
			return "", 0, fmt.Errorf("short domain")
		}
		return string(packet[off+2 : off+2+l]), off + 2 + l, nil
	case 4:
		if len(packet) < off+1+16 {
			return "", 0, fmt.Errorf("short ipv6")
		}
		return net.IP(packet[off+1 : off+17]).String(), off + 17, nil
	default:
		return "", 0, fmt.Errorf("%w: %d", ErrUnsupportedAddressType, packet[off])
	}
}

func buildSocksUDP(addr string, port int, payload []byte) []byte {
	header := []byte{0, 0, 0}
	if ip := net.ParseIP(addr); ip != nil {
		if ip4 := ip.To4(); ip4 != nil {
			header = append(header, 1)
			header = append(header, ip4...)
		} else {
			header = append(header, 4)
			header = append(header, ip.To16()...)
		}
	} else {
		if len(addr) > 255 {
			addr = addr[:255]
		}
		header = append(header, 3, byte(len(addr)))
		header = append(header, []byte(addr)...)
	}
	header = append(header, byte(port>>8), byte(port)) //nolint:gosec
	return append(header, payload...)
}

func writeUDPFrame(w io.Writer, payload []byte) error {
	if len(payload) > maxUDPPayload {
		return fmt.Errorf("udp frame too large: %d", len(payload))
	}
	header := make([]byte, udpFrameHeaderSize)
	binary.BigEndian.PutUint16(header, uint16(len(payload)))
	if _, err := w.Write(header); err != nil {
		return err
	}
	_, err := w.Write(payload)
	return err
}

func readUDPFrame(r io.Reader) ([]byte, error) {
	header := make([]byte, udpFrameHeaderSize)
	if _, err := io.ReadFull(r, header); err != nil {
		return nil, err
	}
	n := int(binary.BigEndian.Uint16(header))
	if n > maxUDPPayload {
		return nil, fmt.Errorf("invalid udp frame size: %d", n)
	}
	payload := make([]byte, n)
	if _, err := io.ReadFull(r, payload); err != nil {
		return nil, err
	}
	return payload, nil
}

func (c *Client) readSocks5Addr(conn net.Conn, addrType byte) (string, error) {
	switch addrType {
	case 1: // IPv4
		buf := make([]byte, 4)
		if _, err := io.ReadFull(conn, buf); err != nil {
			return "", fmt.Errorf("read socks5 ipv4: %w", err)
		}
		return net.IP(buf).String(), nil
	case 3: // Domain
		lenBuf := make([]byte, 1)
		if _, err := io.ReadFull(conn, lenBuf); err != nil {
			return "", fmt.Errorf("read socks5 domain len: %w", err)
		}
		buf := make([]byte, lenBuf[0])
		if _, err := io.ReadFull(conn, buf); err != nil {
			return "", fmt.Errorf("read socks5 domain: %w", err)
		}
		return string(buf), nil
	case 4: // IPv6
		buf := make([]byte, 16)
		if _, err := io.ReadFull(conn, buf); err != nil {
			return "", fmt.Errorf("read socks5 ipv6: %w", err)
		}
		return net.IP(buf).String(), nil
	default:
		return "", fmt.Errorf("%w: %d", ErrUnsupportedAddressType, addrType)
	}
}

func replySuccess() []byte {
	return []byte{5, 0, 0, 1, 0, 0, 0, 0, 0, 0}
}

func replySuccessAddr(ip net.IP, port int) []byte {
	if ip4 := ip.To4(); ip4 != nil {
		return []byte{5, 0, 0, 1, ip4[0], ip4[1], ip4[2], ip4[3], byte(port >> 8), byte(port)} //nolint:gosec
	}
	return replySuccess()
}

func replyHostUnreachable() []byte {
	return []byte{5, 4, 0, 1, 0, 0, 0, 0, 0, 0}
}
