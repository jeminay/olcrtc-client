package videochannel

import (
	"encoding/base64"
	"fmt"
	"image"
	"strings"

	"github.com/makiuchi-d/gozxing"
	zxingqr "github.com/makiuchi-d/gozxing/qrcode"
	qrgen "github.com/skip2/go-qrcode"
)

const (
	quietZone = 10
)

func parseRecoveryLevel(level string) qrgen.RecoveryLevel {
	switch level {
	case "medium":
		return qrgen.Medium
	case "high":
		return qrgen.High
	case "highest":
		return qrgen.Highest
	default:
		return qrgen.Low
	}
}

func renderVisualFrame(payload []byte, width, height int, recoveryLevel string) ([]byte, error) {
	logicalFrameBytes := width * height
	frame := make([]byte, logicalFrameBytes)
	for i := range frame {
		frame[i] = 0xff
	}

	if len(payload) == 0 {
		return frame, nil
	}

	encoded := base64.StdEncoding.EncodeToString(payload)
	qr, err := qrgen.New(encoded, parseRecoveryLevel(recoveryLevel))
	if err != nil {
		return nil, fmt.Errorf("qrcode encode: %w", err)
	}

	bitmap := qr.Bitmap()
	qrSize := len(bitmap)

	scaleW := (width - (quietZone * 2)) / qrSize
	scaleH := (height - (quietZone * 2)) / qrSize
	scale := scaleW
	if scaleH < scale {
		scale = scaleH
	}
	if scale < 1 {
		scale = 1
	}

	totalSize := qrSize * scale
	offsetX := (width - totalSize) / 2
	offsetY := (height - totalSize) / 2

	for y := 0; y < qrSize; y++ {
		for x := 0; x < qrSize; x++ {
			if bitmap[y][x] {
				for sy := 0; sy < scale; sy++ {
					for sx := 0; sx < scale; sx++ {
						pixelX := offsetX + (x * scale) + sx
						pixelY := offsetY + (y * scale) + sy
						if pixelX < width && pixelY < height {
							frame[pixelY*width+pixelX] = 0x00
						}
					}
				}
			}
		}
	}

	return frame, nil
}

func extractVisualPayload(frame []byte, width, height int) ([]byte, error) {
	logicalFrameBytes := width * height
	if len(frame) != logicalFrameBytes {
		return nil, fmt.Errorf("unexpected frame size: %d (expected %dx%d=%d)", len(frame), width, height, logicalFrameBytes)
	}

	img := image.NewGray(image.Rect(0, 0, width, height))
	copy(img.Pix, frame)

	source := gozxing.NewLuminanceSourceFromImage(img)
	binarizer := gozxing.NewHybridBinarizer(source)
	bmp, err := gozxing.NewBinaryBitmap(binarizer)
	if err != nil {
		return nil, fmt.Errorf("bitmap: %w", err)
	}

	reader := zxingqr.NewQRCodeReader()
	hints := make(map[gozxing.DecodeHintType]interface{})
	hints[gozxing.DecodeHintType_TRY_HARDER] = true
	hints[gozxing.DecodeHintType_PURE_BARCODE] = true

	result, err := reader.Decode(bmp, hints)
	if err != nil {
		if strings.Contains(err.Error(), "NotFoundException") {
			return nil, nil
		}
		return nil, fmt.Errorf("decode: %w", err)
	}

	decoded, err := base64.StdEncoding.DecodeString(result.GetText())
	if err != nil {
		return nil, fmt.Errorf("base64 decode: %w", err)
	}

	return decoded, nil
}
