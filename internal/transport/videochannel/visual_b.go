//go:build b

package videochannel

import (
	"fmt"

	"github.com/zarazaex69/b/go"
)

func renderVisualFrameB(payload []byte, width, height int) ([]byte, error) {
	logicalFrameBytes := width * height
	frame := make([]byte, logicalFrameBytes)
	for i := range frame {
		frame[i] = 0xff
	}

	if len(payload) == 0 {
		return frame, nil
	}

	cfg := b.DefaultConfig()
	result, err := b.Encode(payload, cfg)
	if err != nil {
		return nil, fmt.Errorf("b encode: %w", err)
	}

	bmpW := int(result.Width)
	bmpH := int(result.Height)

	scaleW := width / bmpW
	scaleH := height / bmpH
	scale := scaleW
	if scaleH < scale {
		scale = scaleH
	}
	if scale < 1 {
		scale = 1
	}

	totalW := bmpW * scale
	totalH := bmpH * scale
	offsetX := (width - totalW) / 2
	offsetY := (height - totalH) / 2

	for y := 0; y < bmpH; y++ {
		for x := 0; x < bmpW; x++ {
			idx := (y*bmpW + x) * 4
			r := result.RGBA[idx]
			g := result.RGBA[idx+1]
			bb := result.RGBA[idx+2]

			gray := uint8((int(r) + int(g) + int(bb)) / 3)

			for sy := 0; sy < scale; sy++ {
				for sx := 0; sx < scale; sx++ {
					pixelX := offsetX + (x * scale) + sx
					pixelY := offsetY + (y * scale) + sy
					if pixelX < width && pixelY < height {
						frame[pixelY*width+pixelX] = gray
					}
				}
			}
		}
	}

	return frame, nil
}

func extractVisualPayloadB(frame []byte, width, height int) ([]byte, error) {
	logicalFrameBytes := width * height
	if len(frame) != logicalFrameBytes {
		return nil, fmt.Errorf("unexpected frame size: %d (expected %dx%d=%d)", len(frame), width, height, logicalFrameBytes)
	}

	rgba := make([]byte, width*height*4)
	for i := 0; i < width*height; i++ {
		gray := frame[i]
		rgba[i*4] = gray
		rgba[i*4+1] = gray
		rgba[i*4+2] = gray
		rgba[i*4+3] = 255
	}

	cfg := b.DefaultConfig()
	decoded, err := b.Decode(rgba, uint32(width), uint32(height), cfg)
	if err != nil {
		return nil, nil
	}

	return decoded, nil
}
