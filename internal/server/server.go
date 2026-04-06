package server

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"time"

	"github.com/zarazaex69/olcrtc/internal/crypto"
	"github.com/zarazaex69/olcrtc/internal/mux"
	"github.com/zarazaex69/olcrtc/internal/telemost"
)

type Server struct {
	peer        *telemost.Peer
	cipher      *crypto.Cipher
	mux         *mux.Multiplexer
	connections map[uint16]net.Conn
}

type ConnectRequest struct {
	Cmd  string `json:"cmd"`
	Addr string `json:"addr"`
	Port int    `json:"port"`
}

func Run(roomURL, keyHex string) error {
	var key []byte
	var err error

	if keyHex == "" {
		key = make([]byte, 32)
		if _, err := rand.Read(key); err != nil {
			return err
		}
		log.Printf("Generated key: %x", key)
	} else {
		key, err = hex.DecodeString(keyHex)
		if err != nil {
			return err
		}
	}

	cipher, err := crypto.NewCipher(string(key))
	if err != nil {
		return err
	}

	s := &Server{
		cipher:      cipher,
		connections: make(map[uint16]net.Conn),
	}

	s.mux = mux.New(func(frame []byte) error {
		encrypted, err := s.cipher.Encrypt(frame)
		if err != nil {
			return err
		}
		return s.peer.Send(encrypted)
	})

	peer, err := telemost.NewPeer(roomURL, "OlcRTC-Server", s.onData)
	if err != nil {
		return err
	}
	s.peer = peer

	log.Println("Connecting to Telemost...")
	ctx := context.Background()
	if err := peer.Connect(ctx); err != nil {
		return err
	}
	log.Println("Connected to Telemost")

	go peer.WatchConnection(ctx)

	return s.run()
}

func (s *Server) onData(data []byte) {
	plaintext, err := s.cipher.Decrypt(data)
	if err != nil {
		return
	}

	s.mux.HandleFrame(plaintext)
}

func (s *Server) run() error {
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()

	for range ticker.C {
		for _, sid := range s.mux.GetStreams() {
			data := s.mux.ReadStream(sid)
			if len(data) > 0 {
				if conn, exists := s.connections[sid]; exists {
					if _, err := conn.Write(data); err != nil {
						s.mux.CloseStream(sid)
						conn.Close()
						delete(s.connections, sid)
					}
				} else {
					var req ConnectRequest
					if err := json.Unmarshal(data, &req); err == nil && req.Cmd == "connect" {
						go s.handleConnect(sid, req)
					}
				}
			}

			if s.mux.StreamClosed(sid) {
				if conn, exists := s.connections[sid]; exists {
					conn.Close()
					delete(s.connections, sid)
				}
			}
		}
	}

	return nil
}

func (s *Server) handleConnect(sid uint16, req ConnectRequest) {
	addr := fmt.Sprintf("%s:%d", req.Addr, req.Port)
	log.Printf("Connecting sid=%d to %s", sid, addr)

	conn, err := net.DialTimeout("tcp", addr, 10*time.Second)
	if err != nil {
		log.Printf("Connect failed sid=%d: %v", sid, err)
		s.mux.CloseStream(sid)
		return
	}

	s.connections[sid] = conn
	log.Printf("Connected sid=%d", sid)

	go func() {
		buf := make([]byte, 4096)
		for {
			n, err := conn.Read(buf)
			if err != nil {
				if err != io.EOF {
					log.Printf("Read error sid=%d: %v", sid, err)
				}
				s.mux.CloseStream(sid)
				return
			}

			if err := s.mux.SendData(sid, buf[:n]); err != nil {
				log.Printf("Send error sid=%d: %v", sid, err)
				return
			}
		}
	}()
}
