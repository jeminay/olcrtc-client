package videochannel

import (
	"fmt"
	"strings"

	grqr "github.com/zarazaex69/gr/qr"
)

func eccLevel(level string) grqr.ECCLevel {
	switch level {
	case "medium":
		return grqr.ECCMedium
	case "high":
		return grqr.ECCQuartile
	case "highest":
		return grqr.ECCHigh
	default:
		return grqr.ECCLow
	}
}

func renderVisualFrame(payload []byte, width, height int, recoveryLevel string) ([]byte, error) {
	if len(payload) == 0 {
		frame := make([]byte, width*height)
		for i := range frame {
			frame[i] = 0xff
		}
		return frame, nil
	}

	codec, err := grqr.New(grqr.Config{
		FrameW: width,
		FrameH: height,
		Margin: 2,
		ECC:    eccLevel(recoveryLevel),
	})
	if err != nil {
		return nil, fmt.Errorf("qr codec: %w", err)
	}

	return codec.Encode(payload)
}

func extractVisualPayload(frame []byte, width, height int) ([]byte, error) {
	if len(frame) != width*height {
		return nil, fmt.Errorf("unexpected frame size: %d (expected %dx%d=%d)", len(frame), width, height, width*height)
	}

	codec, err := grqr.New(grqr.Config{
		FrameW: width,
		FrameH: height,
		Margin: 2,
	})
	if err != nil {
		return nil, fmt.Errorf("qr codec: %w", err)
	}

	data, err := codec.Decode(frame)
	if err != nil {
		if strings.Contains(err.Error(), "NotFoundException") || strings.Contains(err.Error(), "not found") {
			return nil, nil
		}
		return nil, fmt.Errorf("decode: %w", err)
	}

	return data, nil
}
